"""CLI: ``python -m ingestion {ctgov,fda,load,sample}``."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

import httpx

from common.config import get_settings
from common.storage import FileStorage, Storage, get_storage
from common.warehouse import Warehouse, get_warehouse
from ingestion import ctgov, fda, load, sample

SAMPLE_DIR = Path("data/sample")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ingestion")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ctgov", help="pull interventional studies to landing")
    p.add_argument("--full", action="store_true", help="ignore the watermark")
    p.add_argument("--max-pages", type=int, default=None)

    p = sub.add_parser("fda", help="pull openFDA reaction counts to landing")
    p.add_argument("--max-drugs", type=int, default=800)

    p = sub.add_parser("load", help="load landing (or sample) files into raw tables")
    p.add_argument("--source", choices=["landing", "sample"], default="landing")

    sub.add_parser("sample", help="rebuild the committed snapshot in data/sample/")
    return parser


def _top_names(storage: Storage, wh: Warehouse | None, n: int) -> list[tuple[str, int]]:
    """Top drug names from raw.ctgov_studies when it exists, else from landing files."""
    if wh is not None:
        is_duck = get_settings().warehouse == "duckdb"
        cast = "cast(study as varchar)" if is_duck else "to_varchar(study)"
        try:
            df = wh.query_df(
                f"select {cast} as study from raw.ctgov_studies "
                "qualify row_number() over (partition by nct_id "
                "order by last_update_posted desc, loaded_at desc) = 1"
            )
        except Exception:  # table (or warehouse file) missing -> fall back to landing files
            df = None
        if df is not None and len(df):
            return fda.top_drug_names((json.loads(s) for s in df["study"]), n)
    return fda.top_drug_names(fda.latest_landing_studies(storage), n)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()

    if args.command == "ctgov":
        res = ctgov.pull(get_storage(settings), full=args.full, max_pages=args.max_pages)
        print(f"ctgov: {res.n_studies} studies in {res.n_pages} pages, watermark {res.watermark}")
    elif args.command == "fda":
        storage = get_storage(settings)
        wh: Warehouse | None = None
        if settings.warehouse == "snowflake" or Path(settings.duckdb_path).exists():
            wh = get_warehouse(settings)
        try:
            names = [n for n, _ in _top_names(storage, wh, args.max_drugs)]
        finally:
            if wh is not None:
                wh.close()
        if not names:
            print("fda: no drug names found; run `python -m ingestion ctgov` first")
            return 1
        with httpx.Client(timeout=60) as client:
            fetcher = fda.FdaFetcher(client, api_key=settings.openfda_api_key)
            fres = fda.pull(storage, names, fetcher=fetcher)
        print(
            f"fda: {fres.n_drugs} drugs ({fres.n_empty} with no reports)"
            + ("; stopped early at the request cap" if fres.stopped_early else "")
        )
    elif args.command == "load":
        wh = get_warehouse(settings)
        try:
            if args.source == "sample":
                lres = load.load_files(
                    FileStorage(SAMPLE_DIR),
                    wh,
                    dialect=settings.warehouse,
                    source_label="sample",
                )
            else:
                lres = load.load_files(get_storage(settings), wh, dialect=settings.warehouse)
        finally:
            wh.close()
        print(f"load: {lres.files_loaded} files, {lres.rows_loaded} rows")
    elif args.command == "sample":
        sres = sample.build(SAMPLE_DIR)
        print(f"sample: {sres.n_studies} studies, {sres.n_drugs} drugs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
