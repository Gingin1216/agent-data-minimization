"""Offline evaluation: original SQL coverage and synthetic scopes stay separate.

Run in the repository: python -m evaluation.run_minimality_batch --output-dir PATH
Only a NEW output directory is written; no business database is opened.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path

from secagent import (DataScope, check_field_minimality, check_field_sufficiency,
                      check_row_scope, find_extra_fields)
from secagent.sql_to_scope import sql_to_scope

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "data_minimization_benchmark"


def serialize(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, DataScope):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return {f.name: serialize(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {k: serialize(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [serialize(v) for v in value]
    return value


def load_schema(path):
    # Schema only, isolated in memory. Never connect to the business database.
    with sqlite3.connect(":memory:") as conn:
        conn.executescript(path.read_text(encoding="utf-8-sig"))
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        schema = {}
        for (table,) in tables:
            quoted = table.replace('"', '""')
            schema[table] = {}
            for _, name, declared, *_ in conn.execute(f'PRAGMA table_info("{quoted}")'):
                declared = declared.upper()
                if "INT" in declared:
                    logical = "integer"
                elif any(t in declared for t in ("REAL", "FLOA", "DOUB")):
                    logical = "number"
                elif any(t in declared for t in ("CHAR", "CLOB", "TEXT")):
                    logical = "string"
                else:
                    raise ValueError(f"Unmapped type: {table}.{name}: {declared}")
                schema[table][name] = logical
        # Explicit benchmark abstraction; not inferred from arbitrary TEXT.
        if "expense_date" in schema.get("expense", {}):
            schema["expense"]["expense_date"] = "date_iso"
        return schema


def checks(requested, required, types):
    return serialize({"sufficiency": check_field_sufficiency(requested, required),
                      "extras": find_extra_fields(requested, required),
                      "minimality": check_field_minimality(requested, required),
                      "rows": check_row_scope(requested, required, types)})


def signature(result):
    return {**{k: result[k]["status"] for k in ("sufficiency", "extras", "minimality")},
            "coverage": result["rows"]["coverage"]["status"],
            "no_extra": result["rows"]["no_extra"]["status"]}


def original(spec, schema):
    record = {"task_id": spec["task_id"]}
    if spec.get("scenario") != "database":
        return dict(record, status="unsupported_filesystem")
    required = spec.get("must_visit", {}).get("datascope", [])
    record["required_scope_count"] = len(required)
    if not spec.get("requested_query"):
        return dict(record, status="missing_requested_query")
    query = spec["requested_query"]
    converted = sql_to_scope(query, None, schema)
    record.update(requested_query=query, sql_conversion=serialize(converted))
    if not required:
        record["requirement_limitation"] = "empty_required_scopes_not_interpreted_as_no_data_needed"
    elif len(required) != 1:
        record["requirement_limitation"] = "multiple_required_scopes_not_supported"
    if not converted.ok:
        return dict(record, status="sql_unsupported", reason=converted.reason)
    if "requirement_limitation" in record:
        return dict(record, status="unsupported_requirement")
    record["results"] = checks(converted.scope, required[0], schema[required[0]["table"]])
    return dict(record, status="evaluated_without_accuracy_label")


def controls(spec, schema):
    task_id = spec["task_id"]
    required_list = spec.get("must_visit", {}).get("datascope", [])
    reason = None
    if spec.get("scenario") != "database":
        reason = "filesystem"
    elif spec.get("execution_supported") is False:
        reason = "execution_explicitly_unsupported"
    elif len(required_list) != 1:
        reason = "requires_exactly_one_nonempty_scope"
    if reason:
        return [], {"task_id": task_id, "reason": reason}
    required = required_list[0]
    DataScope.model_validate(required)
    types = schema[required["table"]]
    diagnostic = {"task_id": task_id, "scenario": "requirement_self_check",
                  "track": "consistency_only", "results": checks(required, required, types)}
    records = [diagnostic]
    if diagnostic["results"]["extras"]["status"] != "PROVED":
        return records, {"task_id": task_id, "reason": diagnostic["results"]["extras"]["code"]}
    if diagnostic["results"]["rows"]["status"] != "PROVED":
        return records, {"task_id": task_id, "reason": "requirement_rows_undecided"}

    def add(name, requested, expected, extra=None):
        result = checks(requested, required, types)
        observed = signature(result)
        assertions = {k: observed[k] == v for k, v in expected.items()}
        if extra:
            assertions.update(extra(result))
        records.append({"task_id": task_id, "scenario": name, "track": "synthetic_mutation",
                        "requested_scope": requested, "required_scope": required,
                        "expected": expected, "observed": observed, "assertions": assertions,
                        "passed": all(assertions.values()), "results": result})

    # Known mutations, not authentic Agent SQL or top-level action labels.
    if len(required["output_columns"]) > 1:
        requested = copy.deepcopy(required)
        field = requested["output_columns"].pop()
        add("missing_output", requested, {"sufficiency": "VIOLATED", "minimality": "VIOLATED"},
            lambda r: {"missing_output": field in r["sufficiency"]["missing_output_columns"],
                       "read_still_present": not r["sufficiency"]["missing_read_columns"]})
        requested = copy.deepcopy(required)
        requested["output_columns"].remove(field)
        requested["read_columns"].remove(field)
        add("missing_read_and_output", requested, {"sufficiency": "VIOLATED", "minimality": "VIOLATED"},
            lambda r: {"missing_read": field in r["sufficiency"]["missing_read_columns"],
                       "missing_output": field in r["sufficiency"]["missing_output_columns"]})
    extras = sorted(set(types) - set(required["read_columns"]))
    if extras:
        requested = copy.deepcopy(required)
        field = extras[0]
        requested["read_columns"].append(field)
        requested["output_columns"].append(field)
        add("extra_read_and_output", requested,
            {"sufficiency": "PROVED", "extras": "VIOLATED", "minimality": "VIOLATED",
             "coverage": "PROVED", "no_extra": "PROVED"},
            lambda r: {"extra_read": field in r["extras"]["extra_read_columns"],
                       "extra_output": field in r["extras"]["extra_output_columns"],
                       "removed_read": field in r["minimality"]["removed_read_columns"],
                       "removed_output": field in r["minimality"]["removed_output_columns"],
                       "revalidated": r["minimality"]["final_sufficiency"]["status"] == "PROVED",
                       "final_recheck": r["minimality"]["candidate_one_step_minimal"] is True,
                       "restored_reads": set(r["minimality"]["reduced_scope"]["read_columns"]) == set(required["read_columns"]),
                       "restored_outputs": set(r["minimality"]["reduced_scope"]["output_columns"]) == set(required["output_columns"])})
    if required["row_filter"]["op"] != "true":
        requested = copy.deepcopy(required)
        requested["row_filter"] = {"op": "true"}
        add("all_rows", requested, {"sufficiency": "PROVED", "coverage": "PROVED", "no_extra": "VIOLATED"})
        field = next((f for f in required["read_columns"] if types[f] == "string"), None)
        if field:
            requested = copy.deepcopy(required)
            requested["row_filter"] = {"op": "and", "conditions": [
                {"op": "eq", "field": field, "value": "__control_a__"},
                {"op": "eq", "field": field, "value": "__control_b__"}]}
            add("empty_rows", requested, {"sufficiency": "PROVED", "coverage": "VIOLATED", "no_extra": "PROVED"})
    return records, None


def build_report(spec_dir, schema_path):
    paths = sorted(spec_dir.glob("*.json"))
    if not paths:
        raise ValueError("No TaskSpecs found")
    schema = load_schema(schema_path)
    originals, cases, skipped, hashes = [], [], [], {}
    for path in paths:
        content = path.read_bytes()
        hashes[path.name] = hashlib.sha256(content).hexdigest()
        spec = json.loads(content.decode("utf-8-sig"))
        originals.append(original(spec, schema))
        items, reason = controls(spec, schema)
        cases.extend(items)
        if reason:
            skipped.append(reason)
    mutations = [c for c in cases if c["track"] == "synthetic_mutation"]
    diagnostics = [c for c in cases if c["track"] == "consistency_only"]
    return {"schema_version": "1.0", "source_hashes": hashes,
            "schema_sha256": hashlib.sha256(schema_path.read_bytes()).hexdigest(), "field_types": schema,
            "boundaries": ["No authorization, database execution or max_rows completeness judgement.",
                           "Original SQL unchanged; no reference_sql fallback or injected LIMIT.",
                           "Self checks and synthetic cases are not real-request accuracy.",
                           "date_iso uses YYYYMMDD integers; SQL NULL/calendar/schema constraints excluded.",
                           "Counterexamples are symbolic, not existing database rows."],
            "summary": {"original_total": len(originals),
                        "original_status_counts": dict(Counter(c["status"] for c in originals)),
                        "consistency_checks": len(diagnostics),
                        "consistency_extra_status_counts": dict(Counter(c["results"]["extras"]["status"] for c in diagnostics)),
                        "synthetic_mutations": len(mutations),
                        "synthetic_passed": sum(c["passed"] for c in mutations),
                        "synthetic_failed": sum(not c["passed"] for c in mutations),
                        "synthetic_scenario_counts": dict(Counter(c["scenario"] for c in mutations)),
                        "real_request_accuracy": None},
            "originals": originals, "controls": cases, "control_skips": skipped}


def markdown(report):
    lines = ["# 最小充分性批量评测", "", "原始 SQL、需求自检、人工扰动分开统计。DENY 不等于不充分。", "",
             "## 汇总", "", "```json", json.dumps(report["summary"], ensure_ascii=False, indent=2), "```", "",
             "未统计真实请求准确率：当前没有与各检查维度对应的独立实际请求标签。",
             "人工扰动通过数仅表示已知变更被正确检测，需求自检也不计入准确率。", "",
             "## 原始任务接入", "", "| 任务 | 状态 | 原因 |", "| --- | --- | --- |"]
    for c in report["originals"]:
        reason = c.get("reason", c.get("requirement_limitation", "")).replace("|", "/")
        lines.append(f'| {c["task_id"]} | {c["status"]} | {reason} |')
    lines += ["", "## 需求自检与人工扰动", "", "| 任务 | 场景 | 结果 |", "| --- | --- | --- |"]
    for c in report["controls"]:
        result = ("PASS" if c["passed"] else "FAIL") if "passed" in c else str(signature(c["results"]))
        lines.append(f'| {c["task_id"]} | {c["scenario"]} | {result} |')
    lines += ["", "## 未生成扰动的任务", ""]
    lines += [f'- {c["task_id"]}: {c["reason"]}' for c in report["control_skips"]]
    lines += ["", "## 边界", ""] + ["- " + item for item in report["boundaries"]]
    return "\n".join(lines + ["", "完整输入、删除和重验证据、反例见同目录 result.json。", ""])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec-dir", type=Path, default=BENCHMARK / "dataset" / "task_specs")
    parser.add_argument("--schema", type=Path, default=BENCHMARK / "environment" / "database" / "schema.sql")
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory; no overwrite")
    args = parser.parse_args()
    report = build_report(args.spec_dir, args.schema)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "report.md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 1 if report["summary"]["synthetic_failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
