"""Hand check of the low-enrollment label (spec: "hand-check 200 random matches and
non-matches, and report the precision").

``export_label_sample`` draws TERMINATED trials from marts.fct_trial_outcomes: half where
the dbt regex (dbt/macros/low_enrollment.sql) matched why_stopped
(outcome_class = terminated_low_enrollment), half where it did not (terminated_other). The
regex itself is not duplicated here. A human fills ``human_label`` with 1 (stopped for low
enrollment) or 0 (any other reason); ``label_precision`` then computes the regex's precision
(share of matches a human confirms) and, from the non-matches, its miss rate.
Until someone fills the file in, label precision is pending.
"""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from common.warehouse import Warehouse

DEFAULT_SAMPLE_PATH = Path(__file__).resolve().parents[1] / "docs" / "label_check" / "sample.csv"
SAMPLE_COLUMNS = ["nct_id", "why_stopped", "regex_match", "human_label"]
_TRUE = {"1", "y", "yes", "true", "t"}
_FALSE = {"0", "n", "no", "false", "f"}


def export_label_sample(
    warehouse: Warehouse, out_path: Path = DEFAULT_SAMPLE_PATH, n: int = 200, seed: int = 42
) -> pd.DataFrame:
    """Write n random TERMINATED trials (n/2 regex matches, n/2 non-matches) to a CSV."""
    terminated = warehouse.query_df(
        "select nct_id, why_stopped, "
        "case when outcome_class = 'terminated_low_enrollment' then 1 else 0 end as regex_match "
        "from marts.fct_trial_outcomes where overall_status = 'TERMINATED' order by nct_id"
    )
    matches = terminated[terminated["regex_match"] == 1]
    others = terminated[terminated["regex_match"] == 0]
    n_match = min(n // 2, len(matches))
    n_other = min(n - n_match, len(others))
    n_match = min(n - n_other, len(matches))  # top up if non-matches are short
    sample = pd.concat(
        [
            matches.sample(n=n_match, random_state=seed),
            others.sample(n=n_other, random_state=seed),
        ]
    )
    # Shuffle so the labeller is not reading matches and non-matches in blocks.
    sample = sample.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    sample["regex_match"] = sample["regex_match"].astype(int)
    sample["human_label"] = ""
    sample = sample[SAMPLE_COLUMNS]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sample.to_csv(out_path, index=False, encoding="utf-8", lineterminator="\n")
    return sample


@dataclass(frozen=True)
class LabelCheck:
    precision: float  # confirmed matches / labelled matches
    n_matches_labelled: int
    miss_rate: float | None  # low-enrollment share among labelled non-matches
    n_non_matches_labelled: int


def _parse_label(value: object) -> int | None:
    text = "" if pd.isna(value) else str(value).strip().lower()  # type: ignore[call-overload]
    if text in _TRUE:
        return 1
    if text in _FALSE:
        return 0
    if text == "":
        return None
    raise ValueError(f"human_label must be 1/0 (or yes/no), got {value!r}")


def label_precision(path: Path = DEFAULT_SAMPLE_PATH) -> LabelCheck:
    """Precision of the regex from a filled-in sample file."""
    df = pd.read_csv(path, dtype={"human_label": "string"}, keep_default_na=False)
    df["human"] = df["human_label"].map(_parse_label)
    labelled = df[df["human"].notna()]
    matches = labelled[labelled["regex_match"].astype(int) == 1]
    others = labelled[labelled["regex_match"].astype(int) == 0]
    if matches.empty:
        raise ValueError(f"{path}: no regex matches have a human_label yet")
    return LabelCheck(
        precision=float(matches["human"].astype(int).mean()),
        n_matches_labelled=len(matches),
        miss_rate=None if others.empty else float(others["human"].astype(int).mean()),
        n_non_matches_labelled=len(others),
    )
