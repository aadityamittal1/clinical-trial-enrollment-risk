from typing import Any

import httpx
import pytest
import respx
from tenacity import wait_none

from common.storage import FileStorage, read_jsonl_gz
from ingestion import fda

URL = "https://api.fda.gov/drug/event.json"


def study(nct: str, *interventions: tuple[str, str]) -> dict[str, Any]:
    return {
        "protocolSection": {
            "identificationModule": {"nctId": nct},
            "armsInterventionsModule": {
                "interventions": [{"type": t, "name": n} for t, n in interventions]
            },
        }
    }


def test_top_drug_names_counts_studies_once_and_filters_types() -> None:
    studies = [
        study("N1", ("DRUG", "Aspirin 81 mg"), ("DRUG", "aspirin tablet"), ("DEVICE", "Stent")),
        study("N2", ("DRUG", "Aspirin"), ("BIOLOGICAL", "Pembrolizumab")),
        study("N3", ("DRUG", "Placebo"), ("BIOLOGICAL", "pembrolizumab 200 mg"), ("DRUG", "Zinc")),
        study("N4", ("DRUG", "Pembrolizumab")),
    ]
    assert fda.top_drug_names(studies, 2) == [("pembrolizumab", 3), ("aspirin", 2)]
    assert fda.top_drug_names(studies, 10)[-1] == ("zinc", 1)


def test_top_drug_names_ties_break_alphabetically() -> None:
    studies = [study("N1", ("DRUG", "b")), study("N2", ("DRUG", "a"))]
    assert [n for n, _ in fda.top_drug_names(studies, 5)] == ["a", "b"]


def make_fetcher(**kw: Any) -> tuple[httpx.Client, fda.FdaFetcher]:
    client = httpx.Client()
    kw.setdefault("sleep", lambda _s: None)
    return client, fda.FdaFetcher(client, wait=wait_none(), **kw)


@respx.mock
def test_fetch_ok_and_404_and_api_key() -> None:
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(200, json={"results": [{"term": "NAUSEA", "count": 5}]}),
            httpx.Response(404, json={"error": {"code": "NOT_FOUND"}}),
        ]
    )
    client, f = make_fetcher(api_key="K")
    rec = f.fetch("aspirin")
    assert rec["drug_name"] == "aspirin"
    assert rec["results"] == [{"term": "NAUSEA", "count": 5}]
    assert rec["total_reports"] is None
    assert rec["fetched_at"].endswith("+00:00")
    empty = f.fetch("zzz")
    assert empty["results"] == []
    p = route.calls[0].request.url.params
    assert p["count"] == "patient.reaction.reactionmeddrapt.exact"
    assert 'generic_name:"aspirin"' in p["search"]
    assert p["api_key"] == "K"
    client.close()


@respx.mock
def test_no_api_key_param_when_unset() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"results": []}))
    client, f = make_fetcher()
    f.fetch("x")
    assert "api_key" not in route.calls[0].request.url.params
    client.close()


@respx.mock
def test_retries_5xx() -> None:
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(502), httpx.Response(200, json={"results": []})]
    )
    client, f = make_fetcher()
    assert f.fetch("x")["results"] == []
    assert route.call_count == 2
    client.close()


@respx.mock
def test_pull_stops_cleanly_at_request_cap(tmp_path: Any) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={"results": []}))
    storage = FileStorage(tmp_path)
    client, f = make_fetcher(request_cap=2)
    res = fda.pull(storage, ["a", "b", "c"], fetcher=f, today="2026-11-14")
    assert res.n_drugs == 2 and res.stopped_early
    recs = list(read_jsonl_gz(storage, "fda/2026-11-14/part-00000.jsonl.gz"))
    assert [r["drug_name"] for r in recs] == ["a", "b"]
    client.close()


@respx.mock
def test_pull_stops_cleanly_on_persistent_429(tmp_path: Any) -> None:
    respx.get(URL).mock(
        side_effect=[httpx.Response(200, json={"results": []})] + [httpx.Response(429)] * 20
    )
    storage = FileStorage(tmp_path)
    client, f = make_fetcher(max_attempts=2)
    res = fda.pull(storage, ["a", "b", "c"], fetcher=f, today="2026-11-14")
    assert res.n_drugs == 1 and res.stopped_early
    client.close()


def test_rate_limit_sleeps_between_requests() -> None:
    sleeps: list[float] = []
    times = iter([0.0, 0.01, 0.25])
    f = fda.FdaFetcher(
        httpx.Client(), sleep=sleeps.append, clock=lambda: next(times), per_minute=240
    )
    f._throttle()
    f._throttle()
    assert sleeps == [pytest.approx(0.24)]
