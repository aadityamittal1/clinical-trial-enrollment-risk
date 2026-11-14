from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from common.storage import FileStorage, read_jsonl_gz
from ingestion import sample


def _study(nct: str, status: str) -> dict[str, Any]:
    return {
        "protocolSection": {
            "identificationModule": {"nctId": nct},
            "statusModule": {
                "overallStatus": status,
                "startDateStruct": {"date": "2008-05"},
            },
            "armsInterventionsModule": {"interventions": [{"type": "DRUG", "name": "Aspirin"}]},
        }
    }


@pytest.fixture(autouse=True)
def tiny(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sample, "PAUSE_S", 0)
    monkeypatch.setattr(sample, "START_YEARS", range(2008, 2009))
    monkeypatch.setattr(sample, "COMPLETED_PER_YEAR", 2)
    monkeypatch.setattr(sample, "TERMINATED_PER_YEAR", 1)
    monkeypatch.setattr(sample, "OPEN_QUOTAS", {"RECRUITING": 1})


def _mock(drop_one: bool) -> None:
    def ctgov(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        if "filter.ids" in p:
            ids = p["filter.ids"].split("|")
            if drop_one:
                ids = ids[1:]
            return httpx.Response(200, json={"studies": [_study(i, "COMPLETED") for i in ids]})
        status = p["filter.overallStatus"]
        ids = [f"NCT{status[:3]}{i}" for i in range(5)]
        return httpx.Response(200, json={"studies": [_study(i, status) for i in ids]})

    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(side_effect=ctgov)
    respx.get("https://api.fda.gov/drug/event.json").mock(
        return_value=httpx.Response(200, json={"results": [{"term": "X", "count": 1}]})
    )


@respx.mock
def test_build_writes_snapshot_and_swaps_in(tmp_path: Path) -> None:
    _mock(drop_one=False)
    out = tmp_path / "sample"
    (out / "ctgov" / "old").mkdir(parents=True)
    (out / "ctgov" / "old" / "stale.txt").write_text("x")
    res = sample.build(out, today="2026-01-01")
    assert res.n_studies == 4 and res.n_drugs == 1
    assert not (out / ".build-tmp").exists()
    assert not (out / "ctgov" / "old").exists()
    s = FileStorage(out)
    assert len(list(read_jsonl_gz(s, "ctgov/2026-01-01/part-00000.jsonl.gz"))) == 4
    assert "Total studies: 4" in (out / "README.md").read_text(encoding="utf-8")


@respx.mock
def test_fetch_count_mismatch_leaves_existing_snapshot_untouched(tmp_path: Path) -> None:
    _mock(drop_one=True)
    out = tmp_path / "sample"
    (out / "ctgov").mkdir(parents=True)
    (out / "ctgov" / "keep.txt").write_text("x")
    with pytest.raises(RuntimeError, match="fetched"):
        sample.build(out, today="2026-01-01")
    assert (out / "ctgov" / "keep.txt").exists()
    assert not (out / ".build-tmp").exists()
