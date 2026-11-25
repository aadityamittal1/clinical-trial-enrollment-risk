import json
from typing import Any

import httpx
import pytest
import respx
from tenacity import wait_none

from common.storage import FileStorage, read_jsonl_gz
from ingestion import ctgov

URL = "https://clinicaltrials.gov/api/v2/studies"
WM = "_state/ctgov_watermark.json"


def study(nct: str, updated: str) -> dict[str, Any]:
    return {
        "protocolSection": {
            "identificationModule": {"nctId": nct},
            "statusModule": {"lastUpdatePostDateStruct": {"date": updated}},
        }
    }


def page(studies: list[dict[str, Any]], token: str | None = None) -> httpx.Response:
    body: dict[str, Any] = {"studies": studies}
    if token:
        body["nextPageToken"] = token
    return httpx.Response(200, json=body)


@pytest.fixture
def storage(tmp_path: Any) -> FileStorage:
    return FileStorage(tmp_path)


def run(storage: FileStorage, **kw: Any) -> ctgov.PullResult:
    with httpx.Client() as client:
        return ctgov.pull(storage, client=client, today="2026-11-14", wait=wait_none(), **kw)


def watermark(storage: FileStorage) -> str:
    return str(json.loads(storage.read_bytes(WM))["watermark"])


@respx.mock
def test_paginates_and_writes_one_part_per_page(storage: FileStorage) -> None:
    route = respx.get(URL).mock(
        side_effect=[
            page([study("NCT1", "2026-01-01"), study("NCT2", "2026-01-02")], "tok1"),
            page([study("NCT3", "2026-01-03")]),
        ]
    )
    result = run(storage)
    assert result.n_studies == 3 and result.n_pages == 2
    keys = storage.list_keys("ctgov/2026-11-14/")
    assert keys == [
        "ctgov/2026-11-14/part-00000.jsonl.gz",
        "ctgov/2026-11-14/part-00001.jsonl.gz",
    ]
    ids = [
        s["protocolSection"]["identificationModule"]["nctId"]
        for s in read_jsonl_gz(storage, keys[0])
    ]
    assert ids == ["NCT1", "NCT2"]
    first, second = route.calls[0].request.url.params, route.calls[1].request.url.params
    assert "pageToken" not in first
    assert second["pageToken"] == "tok1"
    assert "AREA[StudyType]INTERVENTIONAL" in first["filter.advanced"]
    assert first["pageSize"] == "1000"


@respx.mock
def test_full_pull_has_no_date_filter_and_sets_watermark(storage: FileStorage) -> None:
    route = respx.get(URL).mock(return_value=page([study("NCT1", "2026-03-05")]))
    run(storage, full=True)
    assert "LastUpdatePostDate" not in route.calls[0].request.url.params["filter.advanced"]
    assert watermark(storage) == "2026-03-05"


@respx.mock
def test_incremental_uses_watermark_and_full_ignores_it(storage: FileStorage) -> None:
    storage.write_bytes(WM, b'{"watermark": "2026-02-01"}')
    route = respx.get(URL).mock(return_value=page([study("NCT9", "2026-04-01")]))
    run(storage)
    params = route.calls[0].request.url.params
    assert "AREA[LastUpdatePostDate]RANGE[2026-02-01,MAX]" in params["filter.advanced"]
    assert params["sort"] == "LastUpdatePostDate:asc"
    assert watermark(storage) == "2026-04-01"
    run(storage, full=True)
    assert "LastUpdatePostDate" not in route.calls[1].request.url.params["filter.advanced"]


@respx.mock
def test_watermark_not_advanced_on_failure(storage: FileStorage) -> None:
    storage.write_bytes(WM, b'{"watermark": "2026-02-01"}')
    respx.get(URL).mock(
        side_effect=[page([study("NCT1", "2026-05-01")], "tok"), httpx.Response(400)]
    )
    with pytest.raises(httpx.HTTPStatusError):
        run(storage)
    assert watermark(storage) == "2026-02-01"


@respx.mock
def test_watermark_kept_when_nothing_new(storage: FileStorage) -> None:
    storage.write_bytes(WM, b'{"watermark": "2026-02-01"}')
    respx.get(URL).mock(return_value=page([]))
    result = run(storage)
    assert result.n_studies == 0
    assert storage.list_keys("ctgov/") == []
    assert watermark(storage) == "2026-02-01"


@respx.mock
def test_retries_on_429_5xx_and_timeouts(storage: FileStorage) -> None:
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(503),
            httpx.ConnectTimeout("slow"),
            page([study("NCT1", "2026-01-01")]),
        ]
    )
    result = run(storage)
    assert result.n_studies == 1
    assert route.call_count == 4


@respx.mock
def test_gives_up_after_max_attempts(storage: FileStorage) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        run(storage, max_attempts=3)
    assert route.call_count == 3


@respx.mock
def test_max_pages_truncates_and_watermark_is_last_seen(storage: FileStorage) -> None:
    respx.get(URL).mock(
        side_effect=[
            page([study("NCT1", "2026-01-01")], "t1"),
            page([study("NCT2", "2026-01-02")], "t2"),
            page([study("NCT3", "2026-01-03")]),
        ]
    )
    result = run(storage, full=True, max_pages=2)
    assert result.n_pages == 2
    assert watermark(storage) == "2026-01-02"


@respx.mock
def test_same_day_rerun_does_not_overwrite_parts(storage: FileStorage) -> None:
    respx.get(URL).mock(return_value=page([study("NCT1", "2026-01-01")]))
    run(storage, full=True)
    run(storage, full=True)
    assert len(storage.list_keys("ctgov/2026-11-14/")) == 2
