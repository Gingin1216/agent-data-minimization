import copy

import pytest

from secagent import (
    DataScope, ScopeCheckStatus as S, TaskScopeComparison, compare_task_scopes,
    create_policy_session, publish_policy, check_database_access,
    run_database_access_with_feedback,
)


TYPES = {("company", "expense"): {"department": "string", "expense_date": "date_iso",
                                      "amount": "number", "category": "string", "remark": "string"}}
SQL = ("SELECT expense_date, amount FROM expense WHERE department='RND' "
       "AND expense_date >= '2026-11-01' AND expense_date <= '2026-11-30' LIMIT 20")


def scope(**updates):
    result = dict(schema_version="1.0", database="company", table="expense", operation="SELECT",
                  read_columns=["department", "expense_date", "amount"],
                  output_columns=["expense_date", "amount"], max_rows=20,
                  row_filter={"op": "and", "conditions": [
                      {"op": "eq", "field": "department", "value": "RND"},
                      {"op": "gte", "field": "expense_date", "value": "2026-11-01"},
                      {"op": "lte", "field": "expense_date", "value": "2026-11-30"}]})
    result.update(updates)
    return result


def inputs():
    authorized = scope(read_columns=list(TYPES[("company", "expense")]),
                       output_columns=list(TYPES[("company", "expense")]), max_rows=200,
                       row_filter={"op": "true"})
    forbidden = scope(read_columns=["remark"], output_columns=["remark"], row_filter={"op": "true"})
    return dict(requested_scopes=[scope()], required_scopes=[scope()],
                authorized_scopes=[authorized], forbidden_scopes=[forbidden], field_types_by_table=TYPES)


def test_real_comparator_satisfies_seven_item_contract_without_mutation():
    kwargs = inputs()
    before = copy.deepcopy(kwargs)
    result = compare_task_scopes(**kwargs)
    assert isinstance(result, TaskScopeComparison)
    for name in ("authorization", "forbidden_clear", "read_sufficiency", "output_sufficiency",
                 "record_sufficiency", "field_minimality", "record_minimality"):
        assert getattr(result, name).status is S.PROVED
    assert result.field_minimality.attempts
    assert kwargs == before


@pytest.mark.parametrize("remove_read", [False, True])
def test_missing_outputs_and_reads_are_independent(remove_read):
    kwargs = inputs()
    requested = kwargs["requested_scopes"][0]
    requested["output_columns"].remove("amount")
    if remove_read:
        requested["read_columns"].remove("amount")
    result = compare_task_scopes(**kwargs)
    assert result.output_sufficiency.status is S.VIOLATED
    assert result.read_sufficiency.status is (S.VIOLATED if remove_read else S.PROVED)
    assert result.missing_output_columns == ("amount",)
    assert result.missing_read_columns == (("amount",) if remove_read else ())
    assert result.field_minimality.status is S.VIOLATED


def test_authorized_extra_field_has_reduction_evidence():
    kwargs = inputs()
    requested = kwargs["requested_scopes"][0]
    requested["read_columns"].append("category")
    requested["output_columns"].append("category")
    result = compare_task_scopes(**kwargs)
    assert result.authorization.status is S.PROVED
    assert result.read_sufficiency.status is S.PROVED
    assert result.extra_read_columns == result.extra_output_columns == ("category",)
    assert result.field_minimality.status is S.VIOLATED
    assert result.field_minimality.removed_read_columns == ("category",)
    assert result.field_minimality.final_sufficiency.status is S.PROVED
    assert result.field_minimality.candidate_one_step_minimal is True


def test_forbidden_result_independent_of_positive_authorization():
    kwargs = inputs()
    kwargs["requested_scopes"][0]["read_columns"].append("remark")
    result = compare_task_scopes(**kwargs)
    assert result.authorization.status is S.PROVED
    assert result.forbidden_clear.status is S.VIOLATED
    assert result.forbidden_clear.requested_results[0].counterexample


def test_filter_only_extra_access_is_preserved_in_minimality_evidence():
    kwargs = inputs()
    kwargs["requested_scopes"][0]["row_filter"]["conditions"].append(
        {"op": "eq", "field": "category", "value": "travel"})
    result = compare_task_scopes(**kwargs)
    assert result.extra_read_columns == ()  # This list is the explicit read diff.
    assert result.field_minimality.status is S.VIOLATED
    assert result.field_minimality.final_extra_fields.extra_filter_columns == ("category",)


@pytest.mark.parametrize("mode", ["wide", "narrow", "wrong_department"])
def test_row_directions_keep_counterexamples_not_fake_scope_differences(mode):
    kwargs = inputs()
    predicate = kwargs["requested_scopes"][0]["row_filter"]
    if mode == "wide":
        kwargs["requested_scopes"][0]["row_filter"] = {"op": "true"}
    elif mode == "narrow":
        predicate["conditions"][2]["value"] = "2026-11-01"
    else:
        predicate["conditions"][0]["value"] = "SLS"
    result = compare_task_scopes(**kwargs)
    assert result.record_sufficiency.status is (S.PROVED if mode == "wide" else S.VIOLATED)
    assert result.record_minimality.status is (S.PROVED if mode == "narrow" else S.VIOLATED)
    for item in (result.record_sufficiency, result.record_minimality):
        if item.status is S.VIOLATED:
            assert item.counterexample
    assert result.missing_record_scopes == result.extra_record_scopes == ()


@pytest.mark.parametrize("side", ["requested_scopes", "required_scopes"])
@pytest.mark.parametrize("value", [[], [scope(), scope()], None, "bad"])
def test_unsupported_task_counts_or_collections_are_unknown(side, value):
    kwargs = inputs()
    kwargs[side] = value
    result = compare_task_scopes(**kwargs)
    assert result.read_sufficiency.status is S.UNKNOWN
    assert result.record_minimality.status is S.UNKNOWN


@pytest.mark.parametrize("bad", [{}, None, scope(max_rows=-1), scope(table="project")])
def test_bad_or_mismatched_required_scope_cannot_look_sufficient(bad):
    kwargs = inputs()
    kwargs["required_scopes"] = [bad]
    result = compare_task_scopes(**kwargs)
    assert result.read_sufficiency.status is not S.PROVED
    assert result.output_sufficiency.status is not S.PROVED
    assert result.record_sufficiency.status is S.UNKNOWN


def test_mutated_model_is_revalidated():
    kwargs = inputs()
    required = DataScope.model_validate(scope())
    required.max_rows = -1
    kwargs["required_scopes"] = [required]
    assert compare_task_scopes(**kwargs).field_minimality.status is S.UNKNOWN


def test_missing_types_keep_field_result_and_unknown_row_result():
    kwargs = inputs()
    kwargs["field_types_by_table"] = {}
    result = compare_task_scopes(**kwargs)
    assert result.read_sufficiency.status is S.PROVED
    assert result.authorization.status is S.UNKNOWN
    assert result.record_sufficiency.status is S.UNKNOWN
    assert result.record_sufficiency.unsupported_reason


def test_required_filter_omission_remains_unknown_for_minimality():
    kwargs = inputs()
    kwargs["required_scopes"][0]["read_columns"].remove("department")
    result = compare_task_scopes(**kwargs)
    assert result.field_minimality.status is S.UNKNOWN


def test_limit_does_not_become_a_task_completeness_proof():
    kwargs = inputs()
    kwargs["requested_scopes"][0]["max_rows"] = 1
    result = compare_task_scopes(**kwargs)
    assert result.record_sufficiency.status is S.PROVED  # Predicate coverage only.
    assert result.record_minimality.status is S.PROVED


def session():
    kwargs = inputs()
    obj = create_policy_session(task_id="comparator-test",
        authorized_data_scopes=kwargs["authorized_scopes"],
        forbidden_data_scopes=kwargs["forbidden_scopes"], required_data_scopes=kwargs["required_scopes"])
    publish_policy(obj, {"company_db_query": [(100, 0, {}, 0)]})
    return obj


def test_member5_entry_records_real_field_and_row_results():
    obj = session()
    trace = check_database_access(obj, "company_db_query", {"sql": SQL}, TYPES,
                                  comparator=compare_task_scopes)
    assert trace.status is S.PROVED
    assert trace.comparison.field_minimality.attempts
    assert obj.decision_traces[-1] == trace


def test_real_feedback_retries_missing_amount_then_executes_once():
    obj = session()
    bad = {"sql": SQL.replace("expense_date, amount", "expense_date")}
    before = copy.deepcopy(bad)
    executed, feedback = [], []

    def retry(args, issue):
        feedback.append(issue)
        return {"sql": SQL}

    result, trace = run_database_access_with_feedback(obj, "company_db_query", bad, TYPES,
        comparator=compare_task_scopes, executor=lambda **args: executed.append(args) or "callback-result",
        retry_agent=retry)
    assert result == "callback-result" and len(executed) == 1
    assert len(feedback) == 1 and "amount" in str(feedback)
    assert obj.decision_traces[0].comparison.missing_output_columns == ("amount",)
    assert trace.status is S.PROVED and trace.attempt == 2
    assert bad == before


def test_unknown_requirement_never_calls_executor():
    obj = session()
    publish_policy(obj, {"company_db_query": [(100, 0, {}, 0)]}, required_data_scopes=[])

    def forbidden_execution(**args):
        raise AssertionError("Unsupported requirements must not execute")

    result, trace = run_database_access_with_feedback(obj, "company_db_query", {"sql": SQL}, TYPES,
        comparator=compare_task_scopes, executor=forbidden_execution,
        retry_agent=lambda args, issue: args)
    assert result is None and trace.status is S.UNKNOWN
    assert len(obj.decision_traces) == 3
