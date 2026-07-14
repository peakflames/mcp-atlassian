# Epic jirafix: JIRA Field Completeness & Null Safety

**Phase:** 1 — JIRA & OAuth Correctness Fixes
**Status:** Complete
**Dependencies:** —

---

## Description

Three correctness fixes to Jira field handling. The `*all` sentinel was being silently substituted with the default field list instead of being forwarded to the Jira API — so `fields='*all'` never actually returned all fields. A null filter was added so that `*all` responses exclude custom fields with null or empty-list values, preventing Jira instances with 2000+ defined custom fields from flooding LLM context. And `DEFAULT_READ_JIRA_FIELDS` is confirmed at exactly the upstream 10-field set.

## Requirements Anchors

| TOR ID | Feature File | Scenario Title |
|--------|--------------|----------------|
| TOR-01-BJ2CyHG | `docs/requirements/01-jira-field-completeness.feature.md` | The jira_get_issue tool shall return every populated field, including custom fields, when called with fields='*all', rather than substituting the default field set |
| TOR-01-9TP0naJ | `docs/requirements/01-jira-field-completeness.feature.md` | The jira_get_issue tool shall return exactly the fields named in an explicit, non-'*all' fields parameter, unaffected by the '*all'-handling fix |
| TOR-01-uzPp7yt | `docs/requirements/01-jira-field-completeness.feature.md` | The jira_get_issue tool shall return a well-formed '*all' response, with no error, for an issue with no populated custom fields beyond the standard set |
| TOR-01-twYUvG9 | `docs/requirements/01-jira-field-completeness.feature.md` | The jira_get_issue tool shall exclude custom fields whose value is null or an empty list from '*all' responses, so that LLM context is not flooded with empty field entries on instances with many defined custom fields |
| TOR-01-sa52UmE | `docs/requirements/01-jira-field-completeness.feature.md` | DEFAULT_READ_JIRA_FIELDS shall contain exactly the 10 essential fields from the upstream sooperset/mcp-atlassian project, with no additional fields, ensuring default responses remain aligned with the upstream contract |

## Key Components

### Backend

- `src/mcp_atlassian/jira/issues.py` — pass `["*all"]` directly to the Jira API instead of expanding into the default field list
- `src/mcp_atlassian/models/jira/issue.py` — null/empty-list filter in `to_simplified_dict()` for `*all` mode
- `src/mcp_atlassian/jira/constants.py` — `DEFAULT_READ_JIRA_FIELDS` at exactly 10 upstream fields

### Tests

- `tests/unit/jira/test_issues.py` — `*all` sentinel behavior + null filter regression guard
- `tests/unit/jira/test_constants.py` — 10-field set assertion
