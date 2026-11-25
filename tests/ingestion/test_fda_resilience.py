from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from tenacity import wait_none

from common.storage import FileStorage, read_jsonl_gz, write_jsonl_gz
from common.warehouse import DuckDBWarehouse
from ingestion import fda, load

URL = "https://api.fda.gov/drug/event.json"


def make_fetcher() -> tuple[httpx.Client, fda.FdaFetcher]:
    client = httpx.Client()
    return client, fda.FdaFetcher(client, wait=wait_none(), sleep=lambda _s: None)


def landed(storage: FileStorage, day: str) -> list[str]:
    return [
        r["drug_name"] for k in storage.list_keys(f"fda/{day}/") for r in read_jsonl_gz(storage, k)
    ]


def study(nct: str, *interventions: tuple[str, str], updated: str = "2026-01-01") -> dict[str, Any]:
    return {
        "protocolSection": {
            "identificationModule": {"nctId": nct},
            "statusModule": {"lastUpdatePostDateStruct": {"date": updated}},
            "armsInterventionsModule": {
                "interventions": [{"type": t, "name": n} for t, n in interventions]
            },
        }
    }


@respx.mock
def test_pull_flushes_chunks_and_survives_per_drug_errors(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.params["search"]
        if '"bad"' in name:
            return httpx.Response(400, json={"error": "bad"})
        if '"boom"' in name:
            return httpx.Response(200, content=b"not json")
        return httpx.Response(200, json={"results": [{"term": "X", "count": 1}]})

    respx.get(URL).mock(side_effect=handler)
    storage = FileStorage(tmp_path)
    client, f = make_fetcher()
    res = fda.pull(storage, ["a", "bad", "boom", "b", "c"], fetcher=f, today="d", chunk=2)
    assert (res.n_drugs, res.n_failed, res.stopped_early) == (3, 2, False)
    assert len(storage.list_keys("fda/d/")) == 2
    assert landed(storage, "d") == ["a", "b", "c"]
    client.close()


@respx.mock
def test_pull_keeps_earlier_records_when_unexpected_error_escapes(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("unexpected")
        return httpx.Response(200, json={"results": []})

    respx.get(URL).mock(side_effect=handler)
    storage = FileStorage(tmp_path)
    client, f = make_fetcher()
    with pytest.raises(RuntimeError):
        fda.pull(storage, ["a", "b", "c", "d"], fetcher=f, today="d", chunk=10)
    assert landed(storage, "d") == ["a", "b"]
    client.close()


@respx.mock
def test_second_run_same_day_skips_landed_drugs(tmp_path: Path) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"results": []}))
    storage = FileStorage(tmp_path)
    client, f = make_fetcher()
    fda.pull(storage, ["a", "b"], fetcher=f, today="d")
    res = fda.pull(storage, ["a", "b", "c"], fetcher=f, today="d")
    assert route.call_count == 3
    assert (res.n_drugs, res.n_skipped) == (1, 2)
    assert sorted(landed(storage, "d")) == ["a", "b", "c"]
    client.close()


@respx.mock
def test_search_value_with_special_characters_is_encoded() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"results": []}))
    client, f = make_fetcher()
    f.fetch('5" a/b+c&d')
    req = route.calls[0].request
    search = req.url.params["search"]
    assert 'activesubstancename:"5 a/b+c&d"' in search
    assert 'generic_name:"5 a/b+c&d"' in search
    assert "%2B" in str(req.url) and "%26" in str(req.url)
    client.close()


def _landing(root: Path) -> FileStorage:
    s = FileStorage(root)
    write_jsonl_gz(
        s,
        "ctgov/d1/part-00000.jsonl.gz",
        [
            study("N1", ("DRUG", "Aspirin 81 mg"), ("DEVICE", "Stent")),
            study("N2", ("DRUG", "Aspirin"), ("BIOLOGICAL", "Pembrolizumab")),
        ],
    )
    write_jsonl_gz(
        s, "ctgov/d2/part-00000.jsonl.gz", [study("N1", ("DRUG", "Zinc"), updated="2026-02-01")]
    )
    return s


def test_top_names_from_landing_uses_latest_version_only(tmp_path: Path) -> None:
    s = _landing(tmp_path)
    assert fda.top_drug_names_from_landing(s, 5) == [
        ("aspirin", 1),
        ("pembrolizumab", 1),
        ("zinc", 1),
    ]


def test_top_names_from_warehouse_sql_and_missing_table(tmp_path: Path) -> None:
    wh = DuckDBWarehouse(tmp_path / "w.duckdb")
    assert fda.top_drug_names_from_warehouse(wh, 5) is None
    load.load_files(_landing(tmp_path / "land"), wh)
    got = fda.top_drug_names_from_warehouse(wh, 5)
    assert got is not None and ("aspirin", 1) in got and ("zinc", 1) in got
    assert fda.top_names(FileStorage(tmp_path / "empty"), wh, 5) == got
    wh.close()
