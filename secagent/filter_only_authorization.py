"""Trusted company filter-only rules from environment spec section 2.4.

These rules supplement existing SELECT grants; they never create table access,
projection access, a larger row boundary, or a larger LIMIT. No published scope
or task annotation is changed. Callers must supply identity from the host.
"""
from collections.abc import Mapping
from dataclasses import dataclass, replace

from .datascope_check import ScopeCheckStatus, ScopeSetCheckResult, check_data_scope_set
from .datascope_v1 import DataScope


@dataclass(frozen=True)
class FilterOnlyRule:
    role: str
    table: str
    field: str
    self_only: bool = False


COMPANY_FILTER_ONLY_RULES = (
    FilterOnlyRule("employee", "employee", "employee_id", True),
    FilterOnlyRule("employee", "expense", "employee_id", True),
    FilterOnlyRule("hr", "employee", "employee_id"),
    FilterOnlyRule("department_manager", "project", "project_id"),
)


def _predicate_fields(node):
    if node.op == "true":
        return set()
    if node.op == "and":
        return set().union(*(_predicate_fields(child) for child in node.conditions))
    return {node.field}


def _rules(scope, identity):
    if (not isinstance(identity, Mapping) or identity.get("tenant") != "company"
            or scope.database != "company" or scope.operation != "SELECT"):
        return ()
    fields = _predicate_fields(scope.row_filter) - set(scope.output_columns)
    return tuple(rule for rule in COMPANY_FILTER_ONLY_RULES
                 if rule.role == identity.get("role") and rule.table == scope.table
                 and rule.field in fields
                 and (not rule.self_only or
                      (type(identity.get("user_id")) is str and identity["user_id"])))


def _scope(value):
    payload = value.model_dump(mode="python", warnings=False) if isinstance(value, DataScope) else value
    return DataScope.model_validate(payload)


def scope_with_filter_dependencies(scope, user_identity):
    """Include only whitelisted fields already used by this scope's predicate.

    This is a detached effective read footprint for task comparison. It does
    not infer task needs from the Agent's SQL, nor confer authorization. Apply
    it independently to requested and required scopes, using their own filters.
    Unlisted omitted predicate fields keep the existing UNKNOWN behavior.
    """
    scope = _scope(scope)
    payload = scope.model_dump()
    payload["read_columns"] = sorted(set(scope.read_columns) |
                                     {rule.field for rule in _rules(scope, user_identity)})
    return DataScope.model_validate(payload)


def check_filter_only_authorization(requested_scopes, authorized_scopes,
                                    field_types_by_table, *, user_identity=None):
    """Check ordinary grants plus the four explicit, host-bound filter rules.

    A temporary contributing scope inherits the original grant's row predicate,
    output fields and LIMIT. For employees it additionally requires id=self;
    the existing SMT containment check must prove this for the entire request.
    Without identity, or for malformed input, ordinary strict checks apply.
    """
    baseline = check_data_scope_set(requested_scopes, authorized_scopes, field_types_by_table)
    if baseline.status is ScopeCheckStatus.PROVED or not isinstance(user_identity, Mapping):
        return baseline
    try:
        if not isinstance(requested_scopes, (list, tuple)) or not isinstance(authorized_scopes, (list, tuple)):
            return baseline
        requested = [_scope(value) for value in requested_scopes]
        authorized = [_scope(value) for value in authorized_scopes]
        results = []
        for request in requested:
            effective = list(authorized)
            for candidate in authorized:
                if (candidate.database, candidate.table, candidate.operation) != (
                        request.database, request.table, request.operation):
                    continue
                for rule in _rules(request, user_identity):
                    if rule.field in candidate.read_columns:
                        continue
                    payload = candidate.model_dump()
                    payload["read_columns"] = sorted(set(candidate.read_columns) | {rule.field})
                    if rule.self_only:
                        guard = {"op": "eq", "field": rule.field, "value": user_identity["user_id"]}
                        payload["row_filter"] = {"op": "and", "conditions": [payload["row_filter"], guard]}
                    effective.append(DataScope.model_validate(payload))
            results.append(check_data_scope_set([request], effective, field_types_by_table))
    except (TypeError, ValueError):
        return baseline
    # Rules apply separately to each request: a filter grant from one request
    # cannot authorize an unrelated read declaration in another request.
    status = (ScopeCheckStatus.VIOLATED if any(r.status is ScopeCheckStatus.VIOLATED for r in results)
              else ScopeCheckStatus.UNKNOWN if any(r.status is not ScopeCheckStatus.PROVED for r in results)
              else ScopeCheckStatus.PROVED)
    items = tuple(replace(item, path=f"requested_scopes[{i}]")
                  for i, result in enumerate(results) for item in result.requested_results)
    return ScopeSetCheckResult(
        status, "filter_only_scope_set_" + status.value.lower(),
        "Checked existing grants and trusted company filter-only rules (environment spec 2.4).",
        path=next((item.path for item in items if item.status is not ScopeCheckStatus.PROVED), None),
        requested_results=items,
    )
