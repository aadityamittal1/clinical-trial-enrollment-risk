"""ClinicalTrials.gov v2 client: interventional studies, incremental by LastUpdatePostDate.

Verified against the live API (2026-11-14):
- ``filter.advanced=AREA[StudyType]INTERVENTIONAL AND AREA[LastUpdatePostDate]RANGE[<date>,MAX]``
- ``sort=LastUpdatePostDate:asc`` (so a truncated pull can still advance the watermark safely)
- cursor pagination: response field ``nextPageToken`` -> request param ``pageToken``
- ``pageSize`` up to 1000, ``filter.overallStatus``, ``filter.ids`` (``|``-separated), ``fields``.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from tenacity.wait import wait_base

from common.storage import Storage, write_jsonl_gz
from ingestion._http import DEFAULT_WAIT, get_with_retry

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
WATERMARK_KEY = "_state/ctgov_watermark.json"
INTERVENTIONAL = "AREA[StudyType]INTERVENTIONAL"


@dataclass
class PullResult:
    n_pages: int
    n_studies: int
    watermark: str | None


def today_utc() -> str:
    return datetime.now(UTC).date().isoformat()


def read_watermark(storage: Storage) -> str | None:
    if not storage.exists(WATERMARK_KEY):
        return None
    value = json.loads(storage.read_bytes(WATERMARK_KEY)).get("watermark")
    return str(value) if value else None


def write_watermark(storage: Storage, watermark: str) -> None:
    storage.write_bytes(WATERMARK_KEY, json.dumps({"watermark": watermark}).encode("utf-8"))


def build_params(
    *,
    since: str | None = None,
    page_size: int = 1000,
    page_token: str | None = None,
    sort: str | None = None,
    extra: dict[str, str] | None = None,
    advanced: list[str] | None = None,
) -> dict[str, str]:
    clauses = [INTERVENTIONAL, *(advanced or [])]
    if since:
        clauses.append(f"AREA[LastUpdatePostDate]RANGE[{since},MAX]")
    params = {"filter.advanced": " AND ".join(clauses), "pageSize": str(page_size)}
    if sort:
        params["sort"] = sort
    if page_token:
        params["pageToken"] = page_token
    params.update(extra or {})
    return params


def study_update_date(study: dict[str, Any]) -> str | None:
    value = (
        study.get("protocolSection", {})
        .get("statusModule", {})
        .get("lastUpdatePostDateStruct", {})
        .get("date")
    )
    return str(value) if value else None


def study_nct_id(study: dict[str, Any]) -> str:
    return str(study["protocolSection"]["identificationModule"]["nctId"])


def pull(
    storage: Storage,
    *,
    client: httpx.Client | None = None,
    full: bool = False,
    max_pages: int | None = None,
    page_size: int = 1000,
    today: str | None = None,
    wait: wait_base = DEFAULT_WAIT,
    max_attempts: int = 6,
) -> PullResult:
    """Pull studies to ``ctgov/<date>/part-NNNNN.jsonl.gz``, one part per page.

    The watermark is the newest ``lastUpdatePostDate`` seen, written only after every
    requested page succeeded. The range filter is inclusive, so the boundary day is
    re-pulled; duplicates are harmless (dbt keeps the latest version per study).
    """
    day = today or today_utc()
    previous = None if full else read_watermark(storage)
    own_client = client is None
    http = client or httpx.Client(timeout=60)
    try:
        existing = len(storage.list_keys(f"ctgov/{day}/"))
        newest = previous
        n_pages = n_studies = 0
        token: str | None = None
        while max_pages is None or n_pages < max_pages:
            params = build_params(
                since=previous,
                page_size=page_size,
                page_token=token,
                sort="LastUpdatePostDate:asc",
            )
            resp = get_with_retry(http, BASE_URL, params, max_attempts=max_attempts, wait=wait)
            body = resp.json()
            studies: list[dict[str, Any]] = body.get("studies", [])
            if studies:
                key = f"ctgov/{day}/part-{existing + n_pages:05d}.jsonl.gz"
                write_jsonl_gz(storage, key, studies)
                n_pages += 1
                n_studies += len(studies)
                for s in studies:
                    d = study_update_date(s)
                    if d and (newest is None or d > newest):
                        newest = d
            token = body.get("nextPageToken")
            if not token:
                break
    finally:
        if own_client:
            http.close()
    if newest and newest != previous:
        write_watermark(storage, newest)
    return PullResult(n_pages=n_pages, n_studies=n_studies, watermark=newest)
