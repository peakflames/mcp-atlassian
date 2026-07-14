# Epic Rm1iZNA — Implementation Handoff

**Epic:** JIRA Null Safety & Upstream Alignment
**Branch:** `feature/epic-Rm1iZNA-jira-null-safety-upstream-alignment`
**Implemented:** 2026-07-14
**Status:** Implemented

---

## What Was Built

### TOR-01-twYUvG9 — Null/empty-list filter for `*all` responses

Added a guard in `to_simplified_dict()` that skips any custom field whose processed
value is `None` or an empty list when `requested_fields == "*all"`. This prevents
thousands of `{"value": null}` entries flooding LLM context on instances (like Archer)
with 2000+ defined custom fields.

The fix is intentionally scoped to the `*all` branch only. The explicit-fields branch
(lines 635–680) is left intact — explicitly-requested fields are returned even if null,
preserving the documented contract for callers who asked for a specific field.

### TOR-01-sa52UmE — DEFAULT_READ_JIRA_FIELDS upstream alignment

`constants.py` already contained exactly the upstream 10 fields on `peakflames/main`.
The 18-field expansion from epic XG1cmts had not been merged into `peakflames/main`,
so no source change was required. The existing tests in `test_constants.py` already
asserted the 10-field state and passed without modification.

---

## Key Files

| File | Change | Purpose |
|---|---|---|
| `src/mcp_atlassian/models/jira/issue.py` | Lines 627–630 added | Null/empty-list filter in `*all` branch |
| `tests/unit/jira/test_issues.py` | `test_get_issue_all_fields_excludes_null_custom_fields` added | TOR-01-twYUvG9 regression guard |
| `src/mcp_atlassian/jira/constants.py` | No change | Already 10 fields — TOR-01-sa52UmE satisfied |
| `tests/unit/jira/test_constants.py` | No change | Already asserted 10-field state |
| `tests/unit/jira/test_search.py` | No change | Expanded-set tests never existed on this branch |

---

## Spec Deviations

| TOR | Deviation |
|---|---|
| TOR-01-sa52UmE | Plan described removing 8 fields from constants.py and updating test_constants.py; in reality peakflames/main already had the 10-field state. No source change needed. Plan was written against XG1cmts branch state. |
| TOR-01-twYUvG9 | Plan referenced test_issues.py line ~1810; file had 1781 lines and no existing `*all` filter tests. New test added adjacent to `test_get_issue_with_all_fields` (line 987). No other deviation. |

---

## TOR Coverage

| TOR ID | Status | Implementation | Test |
|---|---|---|---|
| TOR-01-twYUvG9 | PASS | `issue.py:627–630` | `test_issues.py::test_get_issue_all_fields_excludes_null_custom_fields` |
| TOR-01-sa52UmE | PASS | `constants.py` (no change needed) | `test_constants.py::TestDefaultReadJiraFields` (all 4 tests) |

---

## Verification Results

```
tests/unit/jira/test_constants.py    4 passed
tests/unit/jira/test_issues.py      66 passed  (includes new test)
tests/unit/jira/test_search.py      40 passed
tests/unit/jira/ (full suite)      722 passed  (excluding pre-existing test_attachments.py Windows path failure)
```

Pre-existing quality issues (not introduced by this epic):
- `mypy`: `issue.py:260` — `Statement is unreachable [unreachable]` (pre-existing)
- `ruff`: Several `E501`, `FBT001/FBT002`, `F841`, `BLE001` in `test_issues.py` and `issue.py` (all pre-existing)
