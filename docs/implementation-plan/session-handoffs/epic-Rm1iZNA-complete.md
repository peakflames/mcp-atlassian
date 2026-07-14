# Epic Rm1iZNA: JIRA Null Safety & Upstream Alignment — Complete

**Completed:** 2026-07-14
**Verified by:** Independent review via `/peak-workflow:wrapup-epic Rm1iZNA`

## What Was Built

Added a null/empty-list filter in `models/jira/issue.py`'s `to_simplified_dict()` so that `fields='*all'` responses exclude custom fields with null or empty-list values, preventing Jira instances with 2000+ defined custom fields (like Archer) from flooding LLM context. Confirmed that `DEFAULT_READ_JIRA_FIELDS` in `jira/constants.py` already contained exactly the upstream 10-field set — no source change was needed for that requirement.

## Key Files

| File | Purpose |
|------|---------|
| `src/mcp_atlassian/models/jira/issue.py:627–630` | Null/empty-list filter in `*all` branch of `to_simplified_dict()` |
| `tests/unit/jira/test_issues.py::TestIssuesMixin::test_get_issue_all_fields_excludes_null_custom_fields` | Regression guard for TOR-01-twYUvG9 |
| `src/mcp_atlassian/jira/constants.py` | No change — already contained upstream 10-field set |
| `tests/unit/jira/test_constants.py::TestDefaultReadJiraFields` | Existing 4 tests already covered TOR-01-sa52UmE |

## Key Decisions

- The null filter is intentionally scoped to the `*all` branch only. Explicit-field callers (`elif isinstance(self.requested_fields, list)`) continue to receive null values when a field was specifically requested — preserving the documented contract for callers who asked for a specific field by name.
- `DEFAULT_READ_JIRA_FIELDS` required no source change; the 18-field XG1cmts expansion had never been merged into `peakflames/main`.

## Requirements Implemented

| TOR ID | Feature File | Verdict | Test Reference |
|--------|--------------|---------|----------------|
| TOR-01-twYUvG9 | `docs/requirements/01-jira-field-completeness.feature.md` | PASS | `tests/unit/jira/test_issues.py::TestIssuesMixin::test_get_issue_all_fields_excludes_null_custom_fields` |
| TOR-01-sa52UmE | `docs/requirements/01-jira-field-completeness.feature.md` | PASS | `tests/unit/jira/test_constants.py::TestDefaultReadJiraFields` (4 tests) |

## Verification Summary

### Counts
- TOR Requirements: 2/2 PASS, 0 CANNOT VERIFY
- Quality Gates: 2/2 PASS
- Tests: 722 passed, 0 skipped, 0 failed (excluding pre-existing Windows path failure in `test_attachments.py`)

### Highlights
- ✅ TOR-01-twYUvG9 — null/empty-list filter implemented and tested (`issue.py:627–630`, `test_issues.py::TestIssuesMixin::test_get_issue_all_fields_excludes_null_custom_fields`)
- ✅ TOR-01-sa52UmE — `DEFAULT_READ_JIRA_FIELDS` verified at runtime: exactly 10 fields, zero removed fields present (`test_constants.py::TestDefaultReadJiraFields`)
- ✅ Scoping invariant confirmed — null filter is inside `if self.requested_fields == "*all"` only; explicit-fields branch untouched

### Conclusion
Both TOR requirements are satisfied. The null filter at `issue.py:627–630` correctly skips null and empty-list custom fields in `*all` mode, and `constants.py` contains exactly the 10 upstream fields. Tests faithfully mirror the Gherkin Given/When/Then, 722-test regression suite passes, and no regressions were introduced.

### Manual verification performed: No

## Known Issues / Follow-ups

- Pre-existing `mypy` warning: `issue.py:260` — `Statement is unreachable [unreachable]` (not introduced by this epic)
- Pre-existing `test_attachments.py` Windows path failure — `/tmp/` vs `C:\tmp\` path mismatch (not introduced by this epic)
