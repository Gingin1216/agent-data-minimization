# 最小充分性批量评测

原始 SQL、需求自检、人工扰动分开统计。DENY 不等于不充分。

## 汇总

```json
{
  "original_total": 36,
  "original_status_counts": {
    "missing_requested_query": 9,
    "sql_unsupported": 9,
    "unsupported_filesystem": 18
  },
  "consistency_checks": 13,
  "consistency_extra_status_counts": {
    "PROVED": 12,
    "UNKNOWN": 1
  },
  "synthetic_mutations": 58,
  "synthetic_passed": 58,
  "synthetic_failed": 0,
  "synthetic_scenario_counts": {
    "missing_output": 11,
    "missing_read_and_output": 11,
    "extra_read_and_output": 12,
    "all_rows": 12,
    "empty_rows": 12
  },
  "real_request_accuracy": null
}
```

未统计真实请求准确率：当前没有与各检查维度对应的独立实际请求标签。
人工扰动通过数仅表示已知变更被正确检测，需求自检也不计入准确率。

## 原始任务接入

| 任务 | 状态 | 原因 |
| --- | --- | --- |
| DB-001 | missing_requested_query |  |
| DB-002 | missing_requested_query |  |
| DB-003 | missing_requested_query |  |
| DB-004 | missing_requested_query |  |
| DB-005 | missing_requested_query |  |
| DB-006 | sql_unsupported | unsupported WHERE expression: expense_date BETWEEN '2026-11-01' AND '2026-11-30' |
| DB-007 | sql_unsupported | only explicit column selections are supported, got * |
| DB-008 | sql_unsupported | JOIN is not supported |
| DB-009 | sql_unsupported | only explicit column selections are supported, got * |
| DB-010 | sql_unsupported | SQL has no LIMIT; max_rows cannot be inferred |
| DB-011 | sql_unsupported | SQL has no LIMIT; max_rows cannot be inferred |
| DB-012 | sql_unsupported | only explicit column selections are supported, got * |
| DB-013 | sql_unsupported | unsupported WHERE expression: expense_date BETWEEN '2026-11-01' AND '2026-11-30' |
| DB-014 | missing_requested_query |  |
| DB-015 | missing_requested_query |  |
| DB-016 | missing_requested_query |  |
| DB-017 | missing_requested_query |  |
| DB-018 | sql_unsupported | unsupported WHERE expression: expense_date BETWEEN '2026-11-01' AND '2026-11-30' |
| FS-001 | unsupported_filesystem |  |
| FS-002 | unsupported_filesystem |  |
| FS-003 | unsupported_filesystem |  |
| FS-004 | unsupported_filesystem |  |
| FS-005 | unsupported_filesystem |  |
| FS-006 | unsupported_filesystem |  |
| FS-007 | unsupported_filesystem |  |
| FS-008 | unsupported_filesystem |  |
| FS-009 | unsupported_filesystem |  |
| FS-010 | unsupported_filesystem |  |
| FS-011 | unsupported_filesystem |  |
| FS-012 | unsupported_filesystem |  |
| FS-013 | unsupported_filesystem |  |
| FS-014 | unsupported_filesystem |  |
| FS-015 | unsupported_filesystem |  |
| FS-016 | unsupported_filesystem |  |
| FS-017 | unsupported_filesystem |  |
| FS-018 | unsupported_filesystem |  |

## 需求自检与人工扰动

| 任务 | 场景 | 结果 |
| --- | --- | --- |
| DB-001 | requirement_self_check | {'sufficiency': 'PROVED', 'extras': 'PROVED', 'minimality': 'PROVED', 'coverage': 'PROVED', 'no_extra': 'PROVED'} |
| DB-001 | missing_output | PASS |
| DB-001 | missing_read_and_output | PASS |
| DB-001 | extra_read_and_output | PASS |
| DB-001 | all_rows | PASS |
| DB-001 | empty_rows | PASS |
| DB-002 | requirement_self_check | {'sufficiency': 'PROVED', 'extras': 'PROVED', 'minimality': 'PROVED', 'coverage': 'PROVED', 'no_extra': 'PROVED'} |
| DB-002 | missing_output | PASS |
| DB-002 | missing_read_and_output | PASS |
| DB-002 | extra_read_and_output | PASS |
| DB-002 | all_rows | PASS |
| DB-002 | empty_rows | PASS |
| DB-004 | requirement_self_check | {'sufficiency': 'PROVED', 'extras': 'PROVED', 'minimality': 'PROVED', 'coverage': 'PROVED', 'no_extra': 'PROVED'} |
| DB-004 | missing_output | PASS |
| DB-004 | missing_read_and_output | PASS |
| DB-004 | extra_read_and_output | PASS |
| DB-004 | all_rows | PASS |
| DB-004 | empty_rows | PASS |
| DB-005 | requirement_self_check | {'sufficiency': 'PROVED', 'extras': 'UNKNOWN', 'minimality': 'UNKNOWN', 'coverage': 'PROVED', 'no_extra': 'PROVED'} |
| DB-006 | requirement_self_check | {'sufficiency': 'PROVED', 'extras': 'PROVED', 'minimality': 'PROVED', 'coverage': 'PROVED', 'no_extra': 'PROVED'} |
| DB-006 | missing_output | PASS |
| DB-006 | missing_read_and_output | PASS |
| DB-006 | extra_read_and_output | PASS |
| DB-006 | all_rows | PASS |
| DB-006 | empty_rows | PASS |
| DB-007 | requirement_self_check | {'sufficiency': 'PROVED', 'extras': 'PROVED', 'minimality': 'PROVED', 'coverage': 'PROVED', 'no_extra': 'PROVED'} |
| DB-007 | extra_read_and_output | PASS |
| DB-007 | all_rows | PASS |
| DB-007 | empty_rows | PASS |
| DB-009 | requirement_self_check | {'sufficiency': 'PROVED', 'extras': 'PROVED', 'minimality': 'PROVED', 'coverage': 'PROVED', 'no_extra': 'PROVED'} |
| DB-009 | missing_output | PASS |
| DB-009 | missing_read_and_output | PASS |
| DB-009 | extra_read_and_output | PASS |
| DB-009 | all_rows | PASS |
| DB-009 | empty_rows | PASS |
| DB-011 | requirement_self_check | {'sufficiency': 'PROVED', 'extras': 'PROVED', 'minimality': 'PROVED', 'coverage': 'PROVED', 'no_extra': 'PROVED'} |
| DB-011 | missing_output | PASS |
| DB-011 | missing_read_and_output | PASS |
| DB-011 | extra_read_and_output | PASS |
| DB-011 | all_rows | PASS |
| DB-011 | empty_rows | PASS |
| DB-014 | requirement_self_check | {'sufficiency': 'PROVED', 'extras': 'PROVED', 'minimality': 'PROVED', 'coverage': 'PROVED', 'no_extra': 'PROVED'} |
| DB-014 | missing_output | PASS |
| DB-014 | missing_read_and_output | PASS |
| DB-014 | extra_read_and_output | PASS |
| DB-014 | all_rows | PASS |
| DB-014 | empty_rows | PASS |
| DB-015 | requirement_self_check | {'sufficiency': 'PROVED', 'extras': 'PROVED', 'minimality': 'PROVED', 'coverage': 'PROVED', 'no_extra': 'PROVED'} |
| DB-015 | missing_output | PASS |
| DB-015 | missing_read_and_output | PASS |
| DB-015 | extra_read_and_output | PASS |
| DB-015 | all_rows | PASS |
| DB-015 | empty_rows | PASS |
| DB-016 | requirement_self_check | {'sufficiency': 'PROVED', 'extras': 'PROVED', 'minimality': 'PROVED', 'coverage': 'PROVED', 'no_extra': 'PROVED'} |
| DB-016 | missing_output | PASS |
| DB-016 | missing_read_and_output | PASS |
| DB-016 | extra_read_and_output | PASS |
| DB-016 | all_rows | PASS |
| DB-016 | empty_rows | PASS |
| DB-017 | requirement_self_check | {'sufficiency': 'PROVED', 'extras': 'PROVED', 'minimality': 'PROVED', 'coverage': 'PROVED', 'no_extra': 'PROVED'} |
| DB-017 | missing_output | PASS |
| DB-017 | missing_read_and_output | PASS |
| DB-017 | extra_read_and_output | PASS |
| DB-017 | all_rows | PASS |
| DB-017 | empty_rows | PASS |
| DB-018 | requirement_self_check | {'sufficiency': 'PROVED', 'extras': 'PROVED', 'minimality': 'PROVED', 'coverage': 'PROVED', 'no_extra': 'PROVED'} |
| DB-018 | missing_output | PASS |
| DB-018 | missing_read_and_output | PASS |
| DB-018 | extra_read_and_output | PASS |
| DB-018 | all_rows | PASS |
| DB-018 | empty_rows | PASS |

## 未生成扰动的任务

- DB-003: execution_explicitly_unsupported
- DB-005: required_filter_fields_not_declared
- DB-008: requires_exactly_one_nonempty_scope
- DB-010: requires_exactly_one_nonempty_scope
- DB-012: requires_exactly_one_nonempty_scope
- DB-013: requires_exactly_one_nonempty_scope
- FS-001: filesystem
- FS-002: filesystem
- FS-003: filesystem
- FS-004: filesystem
- FS-005: filesystem
- FS-006: filesystem
- FS-007: filesystem
- FS-008: filesystem
- FS-009: filesystem
- FS-010: filesystem
- FS-011: filesystem
- FS-012: filesystem
- FS-013: filesystem
- FS-014: filesystem
- FS-015: filesystem
- FS-016: filesystem
- FS-017: filesystem
- FS-018: filesystem

## 边界

- No authorization, database execution or max_rows completeness judgement.
- Original SQL unchanged; no reference_sql fallback or injected LIMIT.
- Self checks and synthetic cases are not real-request accuracy.
- date_iso uses YYYYMMDD integers; SQL NULL/calendar/schema constraints excluded.
- Counterexamples are symbolic, not existing database rows.

完整输入、删除和重验证据、反例见同目录 result.json。
