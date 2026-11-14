"""CLI: ``python -m pipeline run [--source sample|live] [--retrain|--skip-train]``."""

import argparse
import logging
import sys
from collections.abc import Sequence

from pipeline.run import StepError, run_pipeline


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pipeline", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="ingest -> load -> dbt -> train -> score -> refresh")
    run.add_argument("--source", choices=["sample", "live"], default="sample")
    group = run.add_mutually_exclusive_group()
    group.add_argument(
        "--retrain", action="store_true", help="train even if a model is already registered"
    )
    group.add_argument(
        "--skip-train",
        action="store_true",
        help="never train; fail if no model is registered",
    )
    run.add_argument("--max-pages", type=int, default=None, help="live only: ctgov page cap")
    run.add_argument("--max-drugs", type=int, default=None, help="live only: openFDA drug cap")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        summary = run_pipeline(
            args.source,
            retrain=args.retrain,
            skip_train=args.skip_train,
            max_pages=args.max_pages,
            max_drugs=args.max_drugs,
        )
    except StepError as exc:
        print(f"pipeline FAILED at {exc}", file=sys.stderr)
        return 1
    print(summary.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
