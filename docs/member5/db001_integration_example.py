"""DB-001 handoff fixture. Uses trusted benchmark rows without creating a database."""

import importlib.util
import json
from pathlib import Path

from secagent import DataScope, create_policy_session, publish_policy


def build_session():
    root = Path(__file__).resolve().parents[2]
    task = json.loads((root / "data_minimization_benchmark/dataset/task_specs/DB-001.json").read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location(
        "trusted_company_fixture", root / "data_minimization_benchmark/environment/database/init_db.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    employees, _, _, _, permissions = fixture.build_rows()
    identity = task["user_identity"]
    employee = next(row for row in employees if row["employee_id"] == identity["user_id"])
    permission = next(row for row in permissions if row[0] == identity["role"] and row[1] == "expense")
    columns = permission[2].split(",")
    authorized = DataScope(
        schema_version="1.0", database="company", table="expense", operation="SELECT",
        read_columns=columns, output_columns=columns,
        row_filter={"op": "eq", "field": "department", "value": employee["department"]},
        max_rows=permission[3],
    )
    session = create_policy_session(
        task_id=task["task_id"], user_identity=identity, user_query=task["user_query"],
        authorized_data_scopes=[authorized],
        forbidden_data_scopes=task["forbidden_scope"]["datascope"],
        required_data_scopes=task["must_visit"]["datascope"],
    )
    publish_policy(session, {"company_db_query": [(100, 0, {}, 0)]}, source="trusted_fixture")
    return session
