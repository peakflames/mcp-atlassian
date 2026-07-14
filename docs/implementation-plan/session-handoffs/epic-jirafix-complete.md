# Epic jirafix: JIRA Field Completeness & Null Safety — Complete

**Completed:** 2026-07-14
**Verified by:** Independent review via `/peak-workflow:wrapup-epic`

## What Was Built

Fixed `jira_get_issue` to pass the `*all` sentinel directly to the Jira REST API instead of silently substituting the default field list — so `fields='*all'` now actually returns every populated field including custom fields. Added a null/empty-list filter in `to_simplified_dict()` so `*all` responses exclude custom fields with null or empty-list values, preventing instances with 2000+ defined custom fields (like Archer) from flooding LLM context. `DEFAULT_READ_JIRA_FIELDS` is confirmed at exactly the upstream 10-field set.

## Key Files

| File | Change | Purpose |
|------|--------|---------|
| `src/mcp_atlassian/jira/issues.py:104–110` | Pass `["*all"]` directly | `*all` sentinel fix |
| `src/mcp_atlassian/models/jira/issue.py:627–630` | Null/empty-list filter in `*all` branch | Null safety |
| `src/mcp_atlassian/jira/constants.py` | No change | Already at 10-field upstream set |
| `tests/unit/jira/test_issues.py` | `*all` sentinel tests + null filter test | TOR-01-BJ2CyHG, TOR-01-uzPp7yt, TOR-01-9TP0naJ, TOR-01-twYUvG9 |
| `tests/unit/jira/test_constants.py` | 4 existing tests | TOR-01-sa52UmE |

## Requirements Implemented

| TOR ID | Feature File | Verdict | Test Reference |
|--------|--------------|---------|----------------|
| TOR-01-BJ2CyHG | `01-jira-field-completeness.feature.md` | PASS | `test_issues.py::test_get_issue_with_all_fields` |
| TOR-01-9TP0naJ | `01-jira-field-completeness.feature.md` | PASS | `test_issues.py::test_get_issue_with_explicit_fields` |
| TOR-01-uzPp7yt | `01-jira-field-completeness.feature.md` | PASS | `test_issues.py::test_get_issue_all_fields_no_custom_fields` |
| TOR-01-twYUvG9 | `01-jira-field-completeness.feature.md` | PASS | `test_issues.py::test_get_issue_all_fields_excludes_null_custom_fields` |
| TOR-01-sa52UmE | `01-jira-field-completeness.feature.md` | PASS | `test_constants.py::TestDefaultReadJiraFields` (4 tests) |

## Verification Summary

### Counts
- TOR Requirements: 5/5 PASS
- Quality Gates: PASS (722 unit tests, ruff, mypy)

### Highlights
- ✅ TOR-01-BJ2CyHG — `*all` passes directly to API; custom field returned (`issues.py:104–110`)
- ✅ TOR-01-twYUvG9 — null/empty-list custom fields excluded from `*all` output (`issue.py:627–630`)
- ✅ TOR-01-sa52UmE — `DEFAULT_READ_JIRA_FIELDS` verified at exactly 10 upstream fields at runtime

### Conclusion
All 5 TOR requirements satisfied. The `*all` sentinel fix and null filter are both scoped correctly — the explicit-fields branch is untouched, preserving the contract for callers who name specific fields. 722-test suite passes with no regressions.

### Manual verification performed: No

## Known Issues / Follow-ups

- Pre-existing mypy warning: `issue.py:260` — `Statement is unreachable [unreachable]` (no behavior impact)
- Pre-existing `test_attachments.py` Windows path failure — `/tmp/` vs `C:\tmp\` (not introduced here)
- Upstream contribution deferred: open a `contrib/` branch PR to `sooperset/mcp-atlassian` for the `*all` sentinel fix and null filter.
