"""Canonical drug-name normalizer (Python twin of the dbt ``stg_drug_names`` logic).

``canonical_drug_name(raw)`` applies these rules, in order, to one raw intervention name.
The rules live in the module-level data structures below so the dbt model can mirror them
one-to-one (SQL ``regexp_replace`` per step).

1. Lowercase.
2. If the string contains any word in ``NULL_WORDS`` (``placebo``) -> ``None``.
3. Remove doses with ``DOSE_PATTERN`` (a number, optionally a range ``10-20``, followed by a
   unit from ``DOSE_UNITS`` and optionally ``/<per-unit>`` from ``DOSE_PER_UNITS``; or a number
   followed by ``%``). Examples: ``10 mg``, ``500mg``, ``5 mg/kg``, ``0.5%``, ``1000 iu``.
4. Replace ``( ) , ; :`` with a space.
5. Drop every whitespace-delimited token that is in ``STRIP_WORDS`` (route and form words:
   oral, tablet, capsule, injection, iv, intravenous, subcutaneous, topical, solution, ...).
6. Collapse whitespace, trim ``-/+&.`` from both ends.
7. Empty result -> ``None``.

Everything else (salts such as ``hcl``, hyphens inside a name) is kept as is.
"""

import re

NULL_WORDS: tuple[str, ...] = ("placebo",)

STRIP_WORDS: tuple[str, ...] = (
    "oral",
    "orally",
    "tablet",
    "tablets",
    "tab",
    "tabs",
    "capsule",
    "capsules",
    "injection",
    "injections",
    "injectable",
    "iv",
    "intravenous",
    "intravenously",
    "subcutaneous",
    "subcutaneously",
    "sc",
    "intramuscular",
    "im",
    "topical",
    "solution",
    "suspension",
    "infusion",
    "cream",
    "gel",
    "ointment",
    "patch",
    "spray",
    "inhaled",
    "inhalation",
    "drops",
)

DOSE_UNITS: tuple[str, ...] = (
    "mg",
    "mcg",
    "ug",
    "µg",
    "g",
    "kg",
    "ng",
    "ml",
    "l",
    "iu",
    "unit",
    "units",
    "u",
    "meq",
    "mmol",
)

DOSE_PER_UNITS: tuple[str, ...] = ("kg", "m2", "m²", "ml", "l", "day", "d", "h", "hr", "dose")

_NUM = r"\d+(?:[.,]\d+)?"
DOSE_PATTERN = re.compile(
    rf"(?<![\w.]){_NUM}(?:\s*-\s*{_NUM})?\s*"
    rf"(?:(?:{'|'.join(DOSE_UNITS)})(?![a-zµ])(?:\s*/\s*(?:{'|'.join(DOSE_PER_UNITS)})(?![a-z²]))?"
    rf"|%)"
)
_PUNCT = re.compile(r"[(),;:]")
_WS = re.compile(r"\s+")
_TRIM = "-/+&. "


def canonical_drug_name(raw: str) -> str | None:
    text = raw.lower()
    words = set(re.findall(r"[a-z]+", text))
    if words & set(NULL_WORDS):
        return None
    text = DOSE_PATTERN.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    strip = set(STRIP_WORDS)
    kept = [t for t in _WS.split(text) if t and t.strip(_TRIM) not in strip]
    result = " ".join(kept).strip(_TRIM)
    return result or None
