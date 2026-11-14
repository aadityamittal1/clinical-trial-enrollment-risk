"""Static checks on the BI definitions (Superset YAML bundle, Power BI PBIP).

Neither Superset nor Power BI Desktop is exercised here: these tests only prove the files
parse, that every mart column they reference exists in the dbt mart contract, and that
UUID / name references between files resolve.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MARTS_DIR = ROOT / "dbt" / "models" / "marts"
SUPERSET = ROOT / "bi" / "superset" / "trial_risk_dashboard"
PBI = ROOT / "bi" / "powerbi"
SM = PBI / "TrialRisk.SemanticModel" / "definition"
REPORT = PBI / "TrialRisk.Report" / "definition"


def _contract() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in sorted(MARTS_DIR.glob("*.yml")):
        for m in yaml.safe_load(f.read_text(encoding="utf-8")).get("models", []):
            out[m["name"]] = {c["name"] for c in m["columns"]}
    return out


CONTRACT = _contract()


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ parsing


def test_contract_has_all_marts():
    assert len(CONTRACT) == 11
    assert "ml_scores_latest" in CONTRACT


def test_all_yaml_and_json_parse():
    files = [
        *SUPERSET.rglob("*.yaml"),
        *PBI.rglob("*.json"),
        *PBI.rglob("*.pbir"),
        *PBI.rglob("*.pbism"),
        *PBI.rglob("*.pbip"),
        *PBI.rglob(".platform"),
    ]
    assert len(files) > 40
    for f in files:
        text = f.read_text(encoding="utf-8")
        if f.suffix == ".yaml":
            assert yaml.safe_load(text) is not None, f
        else:
            json.loads(text)


# ------------------------------------------------------------------ Superset

DATASETS = {
    d["uuid"]: d for d in (_yaml(f) for f in sorted((SUPERSET / "datasets").rglob("*.yaml")))
}
CHARTS = {c["uuid"]: c for c in (_yaml(f) for f in sorted((SUPERSET / "charts").glob("*.yaml")))}
DATABASES = {
    d["uuid"]: d for d in (_yaml(f) for f in sorted((SUPERSET / "databases").glob("*.yaml")))
}
DASHBOARD = _yaml(next((SUPERSET / "dashboards").glob("*.yaml")))


def _sql_refs(sql: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Return ({alias: mart}, [(alias, column), ...]) for a virtual dataset query."""
    aliases = {
        m.group(2): m.group(1) for m in re.finditer(r"\b(?:FROM|JOIN)\s+marts\.(\w+)\s+(\w+)", sql)
    }
    refs = [(a, c) for a, c in re.findall(r"\b(\w+)\.(\w+)\b", sql) if a in aliases]
    return aliases, refs


def test_superset_metadata():
    meta = _yaml(SUPERSET / "metadata.yaml")
    assert meta["type"] == "Dashboard"
    assert meta["version"]


def test_superset_database_has_no_secret():
    for db in DATABASES.values():
        assert db["sqlalchemy_uri"].startswith("duckdb:///")
        assert "@" not in db["sqlalchemy_uri"]


def test_superset_dataset_columns_exist_in_contract():
    assert len(DATASETS) >= 4
    for ds in DATASETS.values():
        assert ds["database_uuid"] in DATABASES, ds["table_name"]
        assert ds["schema"] == "marts"
        declared = {c["column_name"] for c in ds["columns"]}
        if ds["sql"] is None:  # physical dataset on a mart
            assert ds["table_name"] in CONTRACT, ds["table_name"]
            assert declared <= CONTRACT[ds["table_name"]], declared - CONTRACT[ds["table_name"]]
            continue
        aliases, refs = _sql_refs(ds["sql"])
        assert aliases, ds["table_name"]
        for mart in aliases.values():
            assert mart in CONTRACT, f"{ds['table_name']}: unknown mart {mart}"
        for alias, column in refs:
            assert column in CONTRACT[aliases[alias]], (
                f"{ds['table_name']}: {aliases[alias]}.{column}"
            )
        # every declared column is either selected as <alias>.<col> or an `AS` computed column
        computed = set(re.findall(r"\bAS\s+(\w+)", ds["sql"]))
        selected = {c for _, c in refs}
        assert declared <= selected | computed, declared - selected - computed


def test_superset_charts_reference_real_datasets_and_columns():
    assert len(CHARTS) >= 12
    keys_cols = ("groupby", "columns", "all_columns", "x_axis", "entity")
    for chart in CHARTS.values():
        ds = DATASETS[chart["dataset_uuid"]]  # KeyError = dangling uuid
        known = {c["column_name"] for c in ds["columns"]}
        metrics = {m["metric_name"] for m in ds["metrics"]}
        p = chart["params"]
        for k in keys_cols:
            v = p.get(k)
            for name in v if isinstance(v, list) else ([v] if v else []):
                assert name in known, f"{chart['slice_name']}: {k}={name}"
        for k in ("metric", "metrics"):
            v = p.get(k)
            for name in v if isinstance(v, list) else ([v] if v else []):
                assert name in metrics, f"{chart['slice_name']}: {k}={name}"
        assert chart["viz_type"] == p["viz_type"]


def test_superset_dashboard_uuids_resolve():
    pos = DASHBOARD["position"]
    chart_nodes = [n for n in pos.values() if isinstance(n, dict) and n.get("type") == "CHART"]
    assert {n["meta"]["uuid"] for n in chart_nodes} == set(CHARTS), "dashboard and charts disagree"
    tabs = [n for n in pos.values() if isinstance(n, dict) and n.get("type") == "TAB"]
    assert [t["meta"]["text"] for t in tabs] == [
        "Portfolio overview",
        "Enrollment risk",
        "Drug safety context",
    ]
    # every child id exists and points back to its parent
    for node_id, node in pos.items():
        if not isinstance(node, dict):
            continue
        for child in node.get("children", []):
            assert child in pos, f"{node_id} -> missing {child}"
    chart_ids = {n["meta"]["chartId"] for n in chart_nodes}
    assert chart_ids == {
        int(re.search(r"_(\d+)\.yaml$", str(f)).group(1))
        for f in (SUPERSET / "charts").glob("*.yaml")
    }
    for flt in DASHBOARD["metadata"]["native_filter_configuration"]:
        for target in flt["targets"]:
            ds = DATASETS[target["datasetUuid"]]
            assert target["column"]["name"] in {c["column_name"] for c in ds["columns"]}
        for root in flt["scope"]["rootPath"]:
            assert root in pos


# ------------------------------------------------------------------ Power BI / TMDL


def _parse_tables() -> dict[str, dict[str, set[str]]]:
    """{table: {"source": {...}, "calc": {...}, "measures": {...}}} from tables/*.tmdl."""
    out = {}
    for f in sorted((SM / "tables").glob("*.tmdl")):
        text = f.read_text(encoding="utf-8")
        name = re.match(r"table (\w+)", text).group(1)
        assert name == f.stem
        calc = set(re.findall(r"^\tcolumn (\w+) =", text, re.M))
        out[name] = {
            "source": set(re.findall(r"^\t\tsourceColumn: (\w+)$", text, re.M)),
            "calc": calc,
            "measures": set(re.findall(r"^\tmeasure '([^']+)' =", text, re.M)),
            "text": {text},
        }
    return out


TABLES = _parse_tables()


def test_tmdl_has_a_table_for_every_mart_with_exact_columns():
    assert set(TABLES) == set(CONTRACT)
    for name, t in TABLES.items():
        assert t["source"] == CONTRACT[name], f"{name}: {t['source'] ^ CONTRACT[name]}"
        text = next(iter(t["text"]))
        assert "mode: import" in text
        assert f'fnMart("{name}")' in text
        # every sourceColumn also has a `column <name>` declaration
        assert {c for c in re.findall(r"^\tcolumn (\w+)$", text, re.M)} == CONTRACT[name]


def test_tmdl_model_refs_and_parameters():
    model = (SM / "model.tmdl").read_text(encoding="utf-8")
    assert "discourageImplicitMeasures" in model
    for name in TABLES:
        assert f"ref table {name}" in model
    expr = (SM / "expressions.tmdl").read_text(encoding="utf-8")
    for p in ("SnowflakeServer", "SnowflakeWarehouse", "SnowflakeDatabase"):
        assert f"expression {p} =" in expr and f"ref expression {p}" in model
    assert "IsParameterQuery = true" in expr
    assert "ref expression fnMart" in model and "expression fnMart =" in expr
    assert "Snowflake.Databases(SnowflakeServer, SnowflakeWarehouse)" in expr
    assert "DirectQuery" not in "".join(f.read_text(encoding="utf-8") for f in SM.rglob("*.tmdl"))
    # no credentials anywhere in the PBIP folder
    blob = "".join(
        f.read_text(encoding="utf-8", errors="ignore") for f in PBI.rglob("*") if f.is_file()
    )
    assert not re.search(r"password|pwd=|secret|token", blob, re.I)


def test_tmdl_dax_references_resolve():
    dax_ref = re.compile(r"\b(\w+)\[(\w+)\]")
    for name, t in TABLES.items():
        text = next(iter(t["text"]))
        for mart, column in dax_ref.findall(text):
            if mart not in TABLES:
                continue
            ok = column in CONTRACT[mart] or column in TABLES[mart]["calc"]
            assert ok, f"{name}: {mart}[{column}]"
        for measure in re.findall(r"(?<![\w\]])\[([^\]]+)\]", text):
            if measure in CONTRACT[name]:
                continue
            assert any(measure in x["measures"] for x in TABLES.values()), f"{name}: [{measure}]"


def test_tmdl_relationships_resolve():
    rels = re.findall(
        r"^relationship [0-9a-f-]{36}\n((?:\t.*\n)+)",
        (SM / "relationships.tmdl").read_text(encoding="utf-8"),
        re.M,
    )
    assert len(rels) >= 9
    for body in rels:
        ends = re.findall(r"(?:fromColumn|toColumn): (\w+)\.(\w+)", body)
        assert len(ends) == 2
        for table, column in ends:
            assert column in CONTRACT[table], f"{table}.{column}"


def _fields(node):
    """Yield (entity, property, kind) for every Column/Measure field in a JSON tree."""
    if isinstance(node, dict):
        for kind in ("Column", "Measure"):
            if (
                kind in node
                and "Expression" in node[kind]
                and "Entity" in node[kind]["Expression"].get("SourceRef", {})
            ):
                yield node[kind]["Expression"]["SourceRef"]["Entity"], node[kind]["Property"], kind
        for v in node.values():
            yield from _fields(v)
    elif isinstance(node, list):
        for v in node:
            yield from _fields(v)


def test_pbip_wiring():
    pbip = json.loads((PBI / "TrialRisk.pbip").read_text(encoding="utf-8"))
    assert pbip["artifacts"][0]["report"]["path"] == "TrialRisk.Report"
    pbir = json.loads((PBI / "TrialRisk.Report" / "definition.pbir").read_text(encoding="utf-8"))
    target = (PBI / "TrialRisk.Report" / pbir["datasetReference"]["byPath"]["path"]).resolve()
    assert target == (PBI / "TrialRisk.SemanticModel").resolve()
    for sub in ("TrialRisk.Report", "TrialRisk.SemanticModel"):
        assert json.loads((PBI / sub / ".platform").read_text(encoding="utf-8"))["config"][
            "logicalId"
        ]


def test_report_pages_and_visual_fields():
    pages_meta = json.loads((REPORT / "pages" / "pages.json").read_text(encoding="utf-8"))
    order = pages_meta["pageOrder"]
    assert pages_meta["activePageName"] in order
    pages = {}
    for name in order:
        pages[name] = json.loads(
            (REPORT / "pages" / name / "page.json").read_text(encoding="utf-8")
        )
        assert pages[name]["name"] == name
    shown = [p["displayName"] for p in pages.values() if p.get("visibility") != "HiddenInViewMode"]
    assert shown == ["Portfolio overview", "Enrollment risk", "Drug safety context"]
    assert any(p.get("pageBinding", {}).get("type") == "Drillthrough" for p in pages.values())
    assert {p.name for p in (REPORT / "pages").iterdir() if p.is_dir()} == set(order)
    n_visuals = 0
    for name in order:
        for vf in sorted((REPORT / "pages" / name / "visuals").glob("*/visual.json")):
            v = json.loads(vf.read_text(encoding="utf-8"))
            assert v["name"] == vf.parent.name
            n_visuals += 1
            for entity, prop, kind in _fields(v):
                assert entity in TABLES, f"{vf}: table {entity}"
                if kind == "Measure":
                    assert prop in TABLES[entity]["measures"], f"{vf}: measure {entity}.{prop}"
                else:
                    assert prop in CONTRACT[entity] or prop in TABLES[entity]["calc"], (
                        f"{vf}: {entity}.{prop}"
                    )
        for entity, prop, _kind in _fields(pages[name].get("filterConfig", {})):
            assert prop in CONTRACT[entity]
    assert n_visuals >= 20


def test_report_covers_spec_visuals():
    types = []
    for vf in REPORT.rglob("visual.json"):
        types.append(json.loads(vf.read_text(encoding="utf-8"))["visual"]["visualType"])
    for needed in (
        "filledMap",
        "donutChart",
        "stackedColumnChart",
        "tableEx",
        "slicer",
        "card",
        "clusteredBarChart",
    ):
        assert needed in types, needed


@pytest.mark.parametrize("readme", ["superset", "powerbi"])
def test_readmes_state_unverified(readme):
    text = (ROOT / "bi" / readme / "README.md").read_text(encoding="utf-8").lower()
    assert "has not been exercised" in text
