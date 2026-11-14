"""openFDA drug-event reaction counts for the most common trial drug names.

Verified against the live API (2026-11-14): one request per drug,
``count=patient.reaction.reactionmeddrapt.exact`` with a ``search`` on
``patient.drug.activesubstance.activesubstancename`` OR ``patient.drug.openfda.generic_name``
(``openfda.generic_name`` alone misses e.g. pembrolizumab). Unknown drugs return 404, which we
store as an empty ``results`` list. The count endpoint does not return a report total, so
``total_reports`` is ``null``. Counts are reactions across all reports that mention the drug
(co-medications included), not causal rates.
"""

import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from tenacity.wait import wait_base

from common.storage import Storage, read_jsonl_gz, write_jsonl_gz
from ingestion._http import DEFAULT_WAIT, get_with_retry
from ingestion.ctgov import today_utc
from ingestion.drug_names import canonical_drug_name

URL = "https://api.fda.gov/drug/event.json"
DRUG_TYPES = ("DRUG", "BIOLOGICAL")
DAILY_CAP_NO_KEY = 1000
DAILY_CAP_WITH_KEY = 120000


class FdaStop(Exception):
    """Raised when the daily cap (or persistent 429) means we should stop cleanly."""


@dataclass
class FdaPullResult:
    n_drugs: int
    n_empty: int
    stopped_early: bool


def drug_names_in_study(study: dict[str, Any]) -> set[str]:
    interventions = (
        study.get("protocolSection", {}).get("armsInterventionsModule", {}).get("interventions", [])
    )
    names: set[str] = set()
    for item in interventions:
        if item.get("type") in DRUG_TYPES:
            canon = canonical_drug_name(str(item.get("name", "")))
            if canon:
                names.add(canon)
    return names


def top_drug_names(studies: Iterable[dict[str, Any]], n: int) -> list[tuple[str, int]]:
    """Top ``n`` canonical names by number of distinct studies (ties: alphabetical)."""
    counts: Counter[str] = Counter()
    for study in studies:
        counts.update(drug_names_in_study(study))
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def latest_landing_studies(storage: Storage) -> Iterable[dict[str, Any]]:
    """All landed studies, latest landed version per NCT id."""
    latest: dict[str, dict[str, Any]] = {}
    for key in storage.list_keys("ctgov/"):
        for study in read_jsonl_gz(storage, key):
            nct = study["protocolSection"]["identificationModule"]["nctId"]
            latest[nct] = study
    return latest.values()


class FdaFetcher:
    def __init__(
        self,
        client: httpx.Client,
        *,
        api_key: str | None = None,
        per_minute: int = 240,
        request_cap: int | None = None,
        max_attempts: int = 6,
        wait: wait_base = DEFAULT_WAIT,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._interval = 60.0 / per_minute
        self._cap = request_cap or (DAILY_CAP_WITH_KEY if api_key else DAILY_CAP_NO_KEY)
        self._max_attempts = max_attempts
        self._wait = wait
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self.requests = 0

    def _throttle(self) -> None:
        now = self._clock()
        if self._last is not None:
            gap = self._interval - (now - self._last)
            if gap > 0:
                self._sleep(gap)
                now = self._clock()
        self._last = now

    def fetch(self, drug_name: str) -> dict[str, Any]:
        if self.requests >= self._cap:
            raise FdaStop(f"request cap {self._cap} reached")
        self._throttle()
        self.requests += 1
        quoted = drug_name.replace('"', "")
        params = {
            "search": (
                f'patient.drug.activesubstance.activesubstancename:"{quoted}" '
                f'patient.drug.openfda.generic_name:"{quoted}"'
            ),
            "count": "patient.reaction.reactionmeddrapt.exact",
        }
        if self._api_key:
            params["api_key"] = self._api_key
        try:
            resp = get_with_retry(
                self._client,
                URL,
                params,
                max_attempts=self._max_attempts,
                wait=self._wait,
                ok_statuses=(404,),
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise FdaStop("persistent 429 (daily cap?)") from exc
            raise
        results: list[dict[str, Any]] = []
        if resp.status_code != 404:
            results = [
                {"term": str(r["term"]), "count": int(r["count"])}
                for r in resp.json().get("results", [])
            ]
        return {
            "drug_name": drug_name,
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "total_reports": None,
            "results": results,
        }


def pull(
    storage: Storage,
    names: Iterable[str],
    *,
    fetcher: FdaFetcher,
    today: str | None = None,
) -> FdaPullResult:
    """Fetch each name and write ``fda/<date>/part-NNNNN.jsonl.gz`` (one part per run)."""
    day = today or today_utc()
    records: list[dict[str, Any]] = []
    stopped = False
    for name in names:
        try:
            records.append(fetcher.fetch(name))
        except FdaStop:
            stopped = True
            break
    if records:
        part = len(storage.list_keys(f"fda/{day}/"))
        write_jsonl_gz(storage, f"fda/{day}/part-{part:05d}.jsonl.gz", records)
    return FdaPullResult(
        n_drugs=len(records),
        n_empty=sum(1 for r in records if not r["results"]),
        stopped_early=stopped,
    )
