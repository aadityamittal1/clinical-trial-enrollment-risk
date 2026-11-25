import pytest

from ingestion.drug_names import canonical_drug_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Pembrolizumab", "pembrolizumab"),
        ("  Metformin   HCl  ", "metformin hcl"),
        ("Metformin 500 mg", "metformin"),
        ("Metformin 500mg tablet", "metformin"),
        ("Aspirin 81 mg Oral Tablet", "aspirin"),
        ("Trastuzumab 5 mg/kg IV", "trastuzumab"),
        ("Bevacizumab 7.5 mg/kg intravenous infusion", "bevacizumab"),
        ("Lidocaine 0.5% topical solution", "lidocaine"),
        ("Insulin glargine subcutaneous injection", "insulin glargine"),
        ("Amoxicillin capsules", "amoxicillin"),
        ("Ibuprofen (200 mg)", "ibuprofen"),
        ("Vitamin D3 1000 IU", "vitamin d3"),
        ("Placebo", None),
        ("Placebo tablet", None),
        ("Matching placebo oral solution", None),
        ("", None),
        ("   ", None),
        ("10 mg", None),
        ("oral tablet", None),
        ("Pembrolizumab, 200 mg", "pembrolizumab"),
        ("Nab-paclitaxel", "nab-paclitaxel"),
    ],
)
def test_canonical_drug_name(raw: str, expected: str | None) -> None:
    assert canonical_drug_name(raw) == expected


def test_rules_are_exposed_as_data() -> None:
    from ingestion.drug_names import NULL_WORDS, STRIP_WORDS

    assert "placebo" in NULL_WORDS
    assert {"oral", "tablet", "capsule", "injection", "iv"} <= set(STRIP_WORDS)
