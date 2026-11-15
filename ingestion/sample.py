"""Build the committed sample snapshot in ``data/sample/`` from the live APIs.

Method (deterministic given the registry state on the build date):
1. For each stratum, list every matching NCT id (``fields=NCTId``, 1000 per page).
2. Pick ``k`` ids per stratum by ordering ``sha1(nct_id)`` (a fixed pseudo-random sample).
3. Fetch the full study records for the chosen ids (``filter.ids``, 100 per request) and
   write them, sorted by NCT id, 100 studies per part file, in the normal landing format.
4. Take the 60 most common canonical DRUG/BIOLOGICAL names in the sample and fetch their
   openFDA reaction counts.
"""

import hashlib
import shutil
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from common.storage import FileStorage, write_jsonl_gz
from ingestion import ctgov, fda
from ingestion._http import get_with_retry

START_YEARS = range(2008, 2021)
COMPLETED_PER_YEAR = 110
TERMINATED_PER_YEAR = 45
OPEN_QUOTAS: dict[str, int] = {
    "RECRUITING": 400,
    "NOT_YET_RECRUITING": 400,
    "WITHDRAWN": 60,
    "SUSPENDED": 40,
    "ACTIVE_NOT_RECRUITING": 60,
    "UNKNOWN": 40,
}
N_FDA_DRUGS = 60
PART_SIZE = 100
PAUSE_S = 0.15

Stratum = tuple[str, str | None, int]  # (status, start-year or None, quota)


def strata() -> list[Stratum]:
    out: list[Stratum] = []
    for year in START_YEARS:
        out.append(("COMPLETED", str(year), COMPLETED_PER_YEAR))
        out.append(("TERMINATED", str(year), TERMINATED_PER_YEAR))
    out.extend((status, None, quota) for status, quota in OPEN_QUOTAS.items())
    return out


def _get(client: httpx.Client, params: dict[str, str]) -> dict[str, Any]:
    time.sleep(PAUSE_S)
    body: dict[str, Any] = get_with_retry(client, ctgov.BASE_URL, params).json()
    return body


def list_ids(client: httpx.Client, status: str, year: str | None) -> list[str]:
    advanced = [f"AREA[StartDate]RANGE[{year}-01-01,{year}-12-31]"] if year else []
    ids: list[str] = []
    token: str | None = None
    while True:
        params = ctgov.build_params(
            page_size=1000,
            page_token=token,
            extra={"filter.overallStatus": status, "fields": "NCTId"},
            advanced=advanced,
        )
        body = _get(client, params)
        ids.extend(ctgov.study_nct_id(s) for s in body.get("studies", []))
        token = body.get("nextPageToken")
        if not token:
            return ids


def pick(ids: list[str], k: int) -> list[str]:
    ranked = sorted(set(ids), key=lambda i: hashlib.sha1(i.encode()).hexdigest())  # noqa: S324
    return sorted(ranked[:k])


def fetch_studies(client: httpx.Client, ids: list[str]) -> list[dict[str, Any]]:
    studies: list[dict[str, Any]] = []
    for i in range(0, len(ids), 100):
        batch = ids[i : i + 100]
        body = _get(client, {"filter.ids": "|".join(batch), "pageSize": "100"})
        studies.extend(body.get("studies", []))
    return studies


@dataclass
class SampleResult:
    n_studies: int
    n_drugs: int
    n_drugs_empty: int


def _start_year(study: dict[str, Any]) -> str:
    date = study["protocolSection"]["statusModule"].get("startDateStruct", {}).get("date", "")
    return str(date)[:4] or "none"


def _status(study: dict[str, Any]) -> str:
    return str(study["protocolSection"]["statusModule"]["overallStatus"])


def build(out_dir: str | Path, today: str | None = None) -> SampleResult:
    day = today or ctgov.today_utc()
    root = Path(out_dir)
    tmp = root / ".build-tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    storage = FileStorage(tmp)
    try:
        with httpx.Client(timeout=120) as client:
            chosen: list[str] = []
            for status, year, quota in strata():
                ids = list_ids(client, status, year)
                picked = pick(ids, quota)
                print(
                    f"{status} {year or '-'}: {len(ids)} matching, sampled {len(picked)}",
                    flush=True,
                )
                chosen.extend(picked)
            chosen = sorted(set(chosen))
            studies = sorted(fetch_studies(client, chosen), key=ctgov.study_nct_id)
            fetched = {ctgov.study_nct_id(s) for s in studies}
            if len(studies) != len(chosen) or fetched != set(chosen):
                missing = sorted(set(chosen) - fetched)
                raise RuntimeError(
                    f"fetched {len(studies)} studies for {len(chosen)} chosen ids; "
                    f"missing e.g. {missing[:5]}"
                )
            for i in range(0, len(studies), PART_SIZE):
                write_jsonl_gz(
                    storage,
                    f"ctgov/{day}/part-{i // PART_SIZE:05d}.jsonl.gz",
                    studies[i : i + PART_SIZE],
                )
            top = fda.top_drug_names(studies, N_FDA_DRUGS)
            fetcher = fda.FdaFetcher(client)
            res = fda.pull(storage, [n for n, _ in top], fetcher=fetcher, today=day)
            if res.stopped_early or res.n_failed or res.n_drugs != len(top):
                raise RuntimeError(f"openFDA pull incomplete: {res}")
        write_readme(tmp, day, studies, top, res)
        # swap in only after everything succeeded
        root.mkdir(parents=True, exist_ok=True)
        for sub in ("ctgov", "fda"):
            shutil.rmtree(root / sub, ignore_errors=True)
            shutil.move(str(tmp / sub), str(root / sub))
        shutil.move(str(tmp / "README.md"), str(root / "README.md"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return SampleResult(len(studies), res.n_drugs, res.n_empty)


def _dir_size_mb(root: Path) -> float:
    total = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    return total / 1_000_000


def write_readme(
    root: Path,
    day: str,
    studies: list[dict[str, Any]],
    top: list[tuple[str, int]],
    res: fda.FdaPullResult,
) -> None:
    by_status = Counter(_status(s) for s in studies)
    closed = [s for s in studies if _status(s) in ("COMPLETED", "TERMINATED")]
    years = sorted({_start_year(s) for s in closed})
    grid = Counter((_start_year(s), _status(s)) for s in closed)
    lines = [
        "# Sample snapshot",
        "",
        f"Build date: {day} (UTC). Rebuild with `python -m ingestion sample` (needs network).",
        "",
        "Contents are landing-format files (gzipped JSON Lines) that `python -m ingestion load "
        "--source sample` loads into `raw.ctgov_studies` and `raw.fda_event_counts`.",
        "",
        "## Counts (the test suite checks these against the files)",
        "",
        f"- Total studies: {len(studies)}",
        f"- openFDA drugs: {res.n_drugs} ({res.n_empty} returned no reports / HTTP 404)",
        f"- Size on disk: {_dir_size_mb(root):.1f} MB",
        "",
        "### Studies by overall status",
        "",
        "| status | studies |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in sorted(by_status.items())],
        "",
        "### Closed studies (COMPLETED / TERMINATED) by start year",
        "",
        "| start year | COMPLETED | TERMINATED |",
        "|---|---|---|",
        *[f"| {y} | {grid[(y, 'COMPLETED')]} | {grid[(y, 'TERMINATED')]} |" for y in years],
        "",
        "## How it was built",
        "",
        "Interventional studies only (`AREA[StudyType]INTERVENTIONAL`). Every matching NCT id "
        "was listed per stratum, then a fixed pseudo-random subset was taken by ordering "
        "`sha1(nct_id)`, so the same registry state gives the same sample. Strata:",
        "",
        f"- COMPLETED: {COMPLETED_PER_YEAR} per start year, 2008-2020",
        f"- TERMINATED: {TERMINATED_PER_YEAR} per start year, 2008-2020",
        *[f"- {k}: {v} (any start date)" for k, v in OPEN_QUOTAS.items()],
        "",
        f"openFDA: the {N_FDA_DRUGS} most common canonical DRUG/BIOLOGICAL names in the "
        "sample (`ingestion/drug_names.py`), one `count=patient.reaction.reactionmeddrapt.exact` "
        "request each. Study records are stored exactly as the API returned them.",
        "",
        "## Names and contact details in the records",
        "",
        "Because records are stored unedited, they include investigator / overall-official "
        "names and central and site contact names, email addresses and phone numbers, exactly "
        "as published by ClinicalTrials.gov (public registry data). No model, mart or "
        "dashboard uses these fields.",
        "",
        "To rebuild the sample: `uv run python -m ingestion sample` (needs network). It "
        "replaces `data/sample/ctgov/`, `data/sample/fda/` and this README with the registry's "
        "current state, so the counts above change, and so do the metrics in `docs/` once the "
        "model is retrained and re-evaluated.",
        "",
        "## Licensing",
        "",
        "ClinicalTrials.gov and openFDA data are U.S. government works (public domain), so "
        "this snapshot can be committed for reproducibility. Do not use it for clinical "
        "decisions; openFDA data is unvalidated.",
        "",
    ]
    (root / "README.md").write_text("\n".join(lines), encoding="utf-8")
