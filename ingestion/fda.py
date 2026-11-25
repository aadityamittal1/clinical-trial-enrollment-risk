"""openFDA drug-event reaction counts for the most common trial drug names.

Verified against the live API (2026-11-14): one request per drug,
``count=patient.reaction.reactionmeddrapt.exact`` with a ``search`` on
``patient.drug.activesubstance.activesubstancename`` OR ``patient.drug.openfda.generic_name``
(``openfda.generic_name`` alone misses e.g. pembrolizumab). Unknown drugs return 404, which we
store as an empty ``results`` list. The count endpoint does not return a report total, so
``total_reports`` is ``null``. Counts are reactions across all reports that mention the drug
(co-medications included), not causal rates.
"""

import logging
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from tenacity.wait import wait_base

from common.storage import Storage, read_jsonl_gz, write_jsonl_gz
from common.warehouse import Warehouse
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
    n_skipped: int = 0
    n_failed: int = 0


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
    return _rank(counts, n)


def landing_drug_names_per_study(storage: Storage) -> dict[str, set[str]]:
    """Stream landed studies; keep only canonical drug names per NCT id (latest file wins)."""
    names: dict[str, set[str]] = {}
    for key in storage.list_keys("ctgov/"):
        for study in read_jsonl_gz(storage, key):
            nct = study["protocolSection"]["identificationModule"]["nctId"]
            names[nct] = drug_names_in_study(study)
    return names


def _rank(counts: Counter[str], n: int) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def top_drug_names_from_landing(storage: Storage, n: int) -> list[tuple[str, int]]:
    counts: Counter[str] = Counter()
    for names in landing_drug_names_per_study(storage).values():
        counts.update(names)
    return _rank(counts, n)


def _warehouse_table_exists(wh: Warehouse) -> bool:
    df = wh.query_df(
        "select count(*) as n from information_schema.tables "
        "where lower(table_schema) = 'raw' and lower(table_name) = 'ctgov_studies'"
    )
    return int(df["n"][0]) > 0


_LATEST = (
    "select nct_id, study from raw.ctgov_studies "
    "qualify row_number() over (partition by nct_id "
    "order by last_update_posted desc, loaded_at desc) = 1"
)
# Interventions are extracted inside the warehouse; only (nct_id, type, name) rows come back.
DUCKDB_INTERVENTIONS_SQL = (
    "select nct_id, json_extract_string(i, '$.type') as itype, "
    "json_extract_string(i, '$.name') as iname from ("
    "select nct_id, unnest(cast(json_extract(study, "
    "'$.protocolSection.armsInterventionsModule.interventions') as json[])) as i "
    f"from ({_LATEST}))"
)
# Static tier: not executed on the build machine.
SNOWFLAKE_INTERVENTIONS_SQL = (
    "select l.nct_id, f.value:type::varchar as itype, f.value:name::varchar as iname "
    f"from ({_LATEST}) l, "
    "lateral flatten(input => l.study:protocolSection.armsInterventionsModule.interventions) f"
)


def top_drug_names_from_warehouse(
    wh: Warehouse, n: int, dialect: str = "duckdb"
) -> list[tuple[str, int]] | None:
    """Top names from ``raw.ctgov_studies``; ``None`` when the table does not exist."""
    if not _warehouse_table_exists(wh):
        return None
    sql = DUCKDB_INTERVENTIONS_SQL if dialect == "duckdb" else SNOWFLAKE_INTERVENTIONS_SQL
    df = wh.query_df(sql)
    per_study: dict[str, set[str]] = {}
    for nct, itype, iname in zip(df["nct_id"], df["itype"], df["iname"], strict=True):
        if itype in DRUG_TYPES and iname:
            canon = canonical_drug_name(str(iname))
            if canon:
                per_study.setdefault(str(nct), set()).add(canon)
    counts: Counter[str] = Counter()
    for names in per_study.values():
        counts.update(names)
    return _rank(counts, n)


def top_names(
    storage: Storage, wh: Warehouse | None, n: int, dialect: str = "duckdb"
) -> list[tuple[str, int]]:
    """Warehouse table when it exists and has rows, otherwise the landing files."""
    if wh is not None:
        ranked = top_drug_names_from_warehouse(wh, n, dialect)
        if ranked:
            return ranked
    return top_drug_names_from_landing(storage, n)


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


CHUNK = 100
log = logging.getLogger(__name__)


def _landed_names(storage: Storage, day: str) -> set[str]:
    done: set[str] = set()
    for key in storage.list_keys(f"fda/{day}/"):
        done.update(str(r["drug_name"]) for r in read_jsonl_gz(storage, key))
    return done


def pull(
    storage: Storage,
    names: Iterable[str],
    *,
    fetcher: FdaFetcher,
    today: str | None = None,
    chunk: int = CHUNK,
) -> FdaPullResult:
    """Fetch each name and write ``fda/<date>/part-NNNNN.jsonl.gz``, one part per ``chunk``.

    Drugs already landed today are skipped, so a retry or second run the same day neither
    repeats requests (daily cap) nor duplicates rows. A per-drug HTTP or decoding error
    (other than 429) is logged and skipped; records are flushed every ``chunk`` drugs so a
    crash loses at most one chunk.
    """
    day = today or today_utc()
    done = _landed_names(storage, day)
    buffer: list[dict[str, Any]] = []
    n_fetched = n_empty = n_skipped = n_failed = 0
    stopped = False

    def flush() -> None:
        if buffer:
            part = len(storage.list_keys(f"fda/{day}/"))
            write_jsonl_gz(storage, f"fda/{day}/part-{part:05d}.jsonl.gz", buffer)
            buffer.clear()

    try:
        for name in names:
            if name in done:
                n_skipped += 1
                continue
            try:
                rec = fetcher.fetch(name)
            except FdaStop:
                stopped = True
                break
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                log.warning("openFDA fetch failed for %r: %s", name, exc)
                n_failed += 1
                continue
            buffer.append(rec)
            done.add(name)
            n_fetched += 1
            n_empty += 0 if rec["results"] else 1
            if len(buffer) >= chunk:
                flush()
    finally:
        flush()
    return FdaPullResult(
        n_drugs=n_fetched,
        n_empty=n_empty,
        stopped_early=stopped,
        n_skipped=n_skipped,
        n_failed=n_failed,
    )
