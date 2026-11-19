import copy

import pytest

from secagent import (DataScope, ScopeCheckStatus, check_data_scope,
                      check_data_scope_set, check_forbidden_scope,
                      check_forbidden_scope_set)


TYPES = {"name": "string", "salary": "integer"}
BY_TABLE = {("company", "employee"): TYPES}


def scope(**changes):
    result = dict(schema_version="1.0", database="company", table="employee",
                  operation="SELECT", read_columns=["name"], output_columns=["name"],
                  row_filter={"op": "true"}, max_rows=10)
    result.update(changes)
    return result


@pytest.mark.parametrize("collection", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_undeclared_predicate_read_cannot_bypass_authorization(collection, nested):
    predicate = {"op": "gt", "field": "salary", "value": 10000}
    if nested:
        predicate = {"op": "and", "conditions": [
            {"op": "eq", "field": "name", "value": "Alice"}, predicate]}
    requested, authorized = scope(row_filter=predicate), scope()
    before = copy.deepcopy(requested)
    result = (check_data_scope_set([requested], [authorized], BY_TABLE) if collection
              else check_data_scope(requested, authorized, TYPES))
    assert result.status is ScopeCheckStatus.VIOLATED
    assert requested == before


def test_predicate_read_can_be_authorized_without_output_permission():
    requested = scope(row_filter={"op": "gt", "field": "salary", "value": 10000})
    authorized = scope(read_columns=["name", "salary"])
    assert check_data_scope(requested, authorized, TYPES).status is ScopeCheckStatus.PROVED
    assert check_data_scope_set([requested], [authorized], BY_TABLE).status is ScopeCheckStatus.PROVED


def test_authorization_predicate_does_not_itself_grant_read_access():
    predicate = {"op": "gt", "field": "salary", "value": 10000}
    requested, authorized = scope(row_filter=predicate), scope(row_filter=predicate)
    assert check_data_scope(requested, authorized, TYPES).status is ScopeCheckStatus.VIOLATED
    assert check_data_scope_set([requested], [authorized], BY_TABLE).status is ScopeCheckStatus.VIOLATED


@pytest.mark.parametrize("endpoint", ["authorize", "authorize_set", "forbid", "forbid_set"])
@pytest.mark.parametrize("side", [0, 1])
@pytest.mark.parametrize("mutation", ["negative_limit", "undeclared_output", "invalid_nested_filter"])
def test_every_boundary_revalidates_mutable_models(endpoint, side, mutation):
    inputs = [DataScope.model_validate(scope()), DataScope.model_validate(scope())]
    changed = inputs[side]
    if mutation == "negative_limit":
        changed.max_rows = -1
    elif mutation == "undeclared_output":
        changed.output_columns.append("salary")
    else:
        changed.row_filter = {"op": "unknown"}
    if endpoint == "authorize":
        result = check_data_scope(*inputs, TYPES)
    elif endpoint == "authorize_set":
        result = check_data_scope_set([inputs[0]], [inputs[1]], BY_TABLE)
    elif endpoint == "forbid":
        result = check_forbidden_scope(*inputs, TYPES)
    else:
        result = check_forbidden_scope_set([inputs[0]], [inputs[1]], BY_TABLE)
    assert result.status is ScopeCheckStatus.UNKNOWN
    assert result.code == "invalid_datascope"


def test_model_copy_cannot_bypass_revalidation():
    good = DataScope.model_validate(scope())
    bad = good.model_copy(update={"max_rows": -1})
    assert check_data_scope(bad, good, TYPES).status is ScopeCheckStatus.UNKNOWN
    assert check_forbidden_scope(bad, good, TYPES).status is ScopeCheckStatus.UNKNOWN
