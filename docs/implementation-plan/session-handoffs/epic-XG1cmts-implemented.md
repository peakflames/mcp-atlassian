# Epic XG1cmts — Implementation Handoff

**Date:** 2026-07-10
**Branch:** `feature/epic-XG1cmts-jira-field-completeness-fixes`
**Status:** Implemented

---

## What Was Built

Two narrow source-only fixes to `jira/constants.py` and `jira/issues.py`, plus
corresponding tests, so that:

1. `jira_get_issue` with `fields='*all'` now sends the `*all` sentinel directly
   to the Jira REST API instead of silently expanding it into the default field
   list — Jira therefore returns every populated field including custom fields.

2. `DEFAULT_READ_JIRA_FIELDS` now covers 18 fields (up from 10), adding the
   relationship and resolution fields missing from the original set. All callers
   that take the default (`jira_get_issue`, `jira_search`, `jira_get_board_issues`)
   inherit the expansion automatically.

---

## Key Files

| File | Change |
|------|--------|
| `src/mcp_atlassian/jira/constants.py` | Added 8 fields to `DEFAULT_READ_JIRA_FIELDS` (line 205) |
| `src/mcp_atlassian/jira/issues.py` | Changed `*all` branch to use `["*all"]` instead of `list(DEFAULT_READ_JIRA_FIELDS)` (lines 104-110) |
| `tests/unit/jira/test_constants.py` | Updated existing tests for 18-field set; added `test_default_read_jira_fields_includes_relationship_and_resolution_fields` |
| `tests/unit/jira/test_issues.py` | Added three new tests: `test_get_issue_all_fields_returns_custom_fields`, `test_get_issue_all_fields_no_custom_fields_wellformed`, `test_get_issue_explicit_fields_unaffected_by_all_fix` |
| `tests/unit/jira/test_search.py` | Added `test_search_issues_default_fields_include_expanded_set`, `test_get_board_issues_default_fields_include_expanded_set` |

---

## TOR Coverage Verdicts

| TOR ID | Requirement | Test | Impl | Verdict |
|--------|-------------|------|------|---------|
| TOR-01-el7Cazx | Expanded default field set | `test_constants.py::test_default_read_jira_fields_includes_relationship_and_resolution_fields` | `constants.py:205` | **PASS** |
| TOR-01-BJ2CyHG | `*all` returns custom fields | `test_issues.py::test_get_issue_all_fields_returns_custom_fields` | `issues.py:104-110` | **PASS** |
| TOR-01-9TP0naJ | Explicit fields unaffected by fix | `test_issues.py::test_get_issue_explicit_fields_unaffected_by_all_fix` | `issues.py:148` (no change) | **PASS** |
| TOR-01-uzPp7yt | `*all` with only standard fields is well-formed | `test_issues.py::test_get_issue_all_fields_no_custom_fields_wellformed` | `issues.py:104-110` | **PASS** |
| TOR-01-7XlRNfG | `jira_search` inherits expanded defaults | `test_search.py::test_search_issues_default_fields_include_expanded_set` | `search.py:147` (inherit) | **PASS** |
| TOR-01-NfGirOm | `jira_get_board_issues` inherits expanded defaults | `test_search.py::test_get_board_issues_default_fields_include_expanded_set` | `search.py:274` (inherit) | **PASS** |

---

## Verification Results

```
uv run pytest tests/unit/jira/test_constants.py tests/unit/jira/test_issues.py tests/unit/jira/test_search.py -v
# 115 passed in 0.44s

uv run pytest (full suite, excluding 4 pre-existing Windows environment failures)
# 2631 passed, 161 skipped, 4 pre-existing failures (attachments, stdio, date, media)

pre-commit run --all-files
# ruff-format: Passed
# ruff:        Passed
# mypy:        1 pre-existing error in servers/main.py (TTLCache type args) — unrelated
```

**Pre-existing failures (not introduced by this epic):**
- `test_attachments.py::test_download_attachment_success` — Windows `C:\\tmp` vs `/tmp` path mismatch
- `test_stdio_lifecycle.py::test_stdio_homebrew_probe_exits_after_stdin_close` — async mock lifecycle issue
- `test_date.py::test_parse_date_timestamp_boundary_max_valid` — timezone boundary edge case
- `test_media.py::test_mime_type_detection[zip]` — Windows MIME type registry difference

---

## Spec Deviations

None. All 6 TOR IDs implemented and verified as documented.

---

## Deferred Follow-ups (out of code scope)

The epic spec identified three upstream contribution actions requiring `gh` auth
against `sooperset/mcp-atlassian`. These are courtesy upstream-contribution steps
that cannot be pushed without user authorization:

1. File upstream issue: "`fields='*all'` silently truncates to default field list"
2. File upstream issue: "`DEFAULT_READ_JIRA_FIELDS` missing 8 common fields"
3. Update `docs/implementation-plan/upstream-workarounds.md` with resolution status

User action required: run after confirming upstream PR intent:
```bash
# Create contrib branch and open upstream PR (requires gh auth to sooperset)
git checkout -b contrib/jira-field-completeness-fixes upstream/main
git cherry-pick <commit-hash>
gh pr create --repo sooperset/mcp-atlassian ...
```
