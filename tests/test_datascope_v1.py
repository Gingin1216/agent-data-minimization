"""DataScope v1.0 结构单元测试（不涉及权限包含关系或数据库查询）。"""
import unittest
from pydantic import ValidationError
from secagent.datascope_v1 import DataScope


BASE = {
    "schema_version": "1.0",
    "database": "company",
    "table": "employees",
    "operation": "SELECT",
    "read_columns": ["name", "department"],
    "output_columns": ["name"],
    "row_filter": {"op": "eq", "field": "department", "value": "sales"},
    "max_rows": 10,
}


class ScopeSchemaTests(unittest.TestCase):
    def test_roundtrip(self):
        scope = DataScope.model_validate(BASE)
        self.assertEqual(DataScope.model_validate_json(scope.as_json()), scope)

    def test_nested_ast(self):
        payload = dict(BASE)
        payload["row_filter"] = {"op": "and", "conditions": [
            {"op": "eq", "field": "department", "value": "sales"},
            {"op": "in", "field": "city", "values": ["Beijing", "Shanghai"]},
        ]}
        self.assertEqual(DataScope.model_validate(payload).row_filter.op, "and")

    def test_no_extra_keys(self):
        with self.assertRaises(ValidationError):
            DataScope.model_validate({**BASE, "policy_version": 123})

    def test_output_not_subset(self):
        with self.assertRaises(ValidationError):
            DataScope.model_validate({**BASE, "output_columns": ["salary"]})

    def test_only_select(self):
        with self.assertRaises(ValidationError):
            DataScope.model_validate({**BASE, "operation": "DELETE"})

    def test_must_have_limit(self):
        with self.assertRaises(ValidationError):
            DataScope.model_validate({**BASE, "max_rows": 0})

    def test_unsupported_ast(self):
        with self.assertRaises(ValidationError):
            DataScope.model_validate({**BASE, "row_filter": {"op": "or", "conditions": []}})

    def test_explicit_true_filter(self):
        self.assertEqual(DataScope.model_validate({**BASE, "row_filter": {"op": "true"}}).row_filter.op, "true")

    def test_missing_mandatory_field(self):
        no_operation = {k: v for k, v in BASE.items() if k != "operation"}
        with self.assertRaises(ValidationError):
            DataScope.model_validate(no_operation)

if __name__ == "__main__":
    unittest.main()
