"""CLI: python -m ml {train,evaluate,score,label-sample,label-precision}."""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from common.config import get_settings
from ml.split import TEST_END, TRAIN_END, VAL_END


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ml", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    train = sub.add_parser("train", help="train, log to MLflow and register the model")
    train.add_argument("--train-end", type=int, default=TRAIN_END)
    train.add_argument("--val-end", type=int, default=VAL_END)
    train.add_argument("--test-end", type=int, default=TEST_END)
    train.add_argument(
        "--allow-suspicious",
        action="store_true",
        help="register even if test PR-AUC exceeds the leakage threshold",
    )

    evaluate = sub.add_parser("evaluate", help="re-evaluate the registered model, write docs/")
    evaluate.add_argument("--out-dir", type=Path, default=None)

    sub.add_parser("score", help="score open studies into marts.ml_scores")

    label = sub.add_parser("label-sample", help="export the 200-trial label hand-check sample")
    label.add_argument("--out", type=Path, default=None)
    label.add_argument("--n", type=int, default=200)
    label.add_argument("--seed", type=int, default=42)

    precision = sub.add_parser("label-precision", help="precision from a filled-in sample")
    precision.add_argument("--path", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()

    from ml.predict import ModelNotFoundError
    from ml.train import SuspiciousMetricsError

    try:
        if args.command == "train":
            from ml.train import train

            result = train(
                settings,
                train_end=args.train_end,
                val_end=args.val_end,
                test_end=args.test_end,
                allow_suspicious=args.allow_suspicious,
            )
            m = result.metrics
            print(
                f"registered {result.model_name} version {result.model_version} "
                f"(run {result.run_id}); split sizes {result.split_sizes}\n"
                f"PR-AUC val:  lightgbm {m['lightgbm_val_pr_auc']:.3f}  "
                f"logistic {m['logistic_val_pr_auc']:.3f}  "
                f"no-skill {m['no_skill_val_pr_auc']:.3f}\n"
                f"PR-AUC test: lightgbm {m['lightgbm_test_pr_auc']:.3f}  "
                f"logistic {m['logistic_test_pr_auc']:.3f}  "
                f"no-skill {m['no_skill_test_pr_auc']:.3f}\n"
                "(sample oversamples terminated trials: rates are not real-world prevalence)"
            )
        elif args.command == "evaluate":
            from ml.evaluate import DOCS_DIR, evaluate

            report = evaluate(settings, out_dir=args.out_dir or DOCS_DIR)
            t = report["test_registered_model"]
            print(
                f"{report['model_name']} v{report['model_version']} test: PR-AUC "
                f"{t['pr_auc']:.3f} (no-skill {t['no_skill_pr_auc']:.3f}), "
                f"Brier {t['brier']:.3f}, ECE {t['ece']:.3f}"
            )
        elif args.command == "score":
            from ml.score import score

            n = score(settings)
            print(f"appended {n} rows to marts.ml_scores")
        elif args.command == "label-sample":
            from common.warehouse import get_warehouse
            from ml.labels import DEFAULT_SAMPLE_PATH, export_label_sample

            out = args.out or DEFAULT_SAMPLE_PATH
            warehouse = get_warehouse(settings)
            try:
                sample = export_label_sample(warehouse, out, n=args.n, seed=args.seed)
            finally:
                warehouse.close()
            print(
                f"wrote {len(sample)} rows ({int(sample['regex_match'].sum())} regex matches) "
                f"to {out}; fill in human_label (1/0)"
            )
        elif args.command == "label-precision":
            from ml.labels import DEFAULT_SAMPLE_PATH, label_precision

            check = label_precision(args.path or DEFAULT_SAMPLE_PATH)
            print(check)
    except ModelNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except SuspiciousMetricsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
