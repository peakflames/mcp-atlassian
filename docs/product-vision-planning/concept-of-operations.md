# mcp-atlassian Field Completeness & Multi-Instance Auth Patches — Concept of Operations (ConOps)

**Document Version:** 2.0
**Date:** 2026-07-13
**Status:** Draft

*Companion document: `product-vision.md`*

---

## 1. Purpose & Scope

This document describes the operational concept for four source-level fixes in `peakflames/mcp-atlassian`: two JIRA field-completeness defects (`jira/constants.py`, `jira/issues.py`, `jira/search.py`) and two multi-instance OAuth defects (`utils/oauth.py`). These fixes were identified during Archer's Atlassian AI Assist discovery process in the sibling repo `ple-atlassian-mcp`, which deferred five TOR requirements here because they require Python source changes rather than Dockerfile/config changes.

The scope covers only the four code paths named above. It does not cover the unrelated per-project/per-space access control feature or the OAuth proxy deployment guide already in flight on the `docs/atlassian-cloud-oauth-proxy` branch, and it does not cover any Confluence-side behavior, Data Center OAuth, or upstream contribution logistics.

---

## 2. Current State ("As-Is")

| Current Method | Limitation |
|---|---|
| `jira_get_issue(fields='*all')` | `jira/issues.py` line 104 (`if fields_param == "*all" or fields_set == DEFAULT_READ_JIRA_FIELDS:`) routes `*all` into the same branch as the unqualified default call, then uses `list(DEFAULT_READ_JIRA_FIELDS)` at line 109. Returns 10 fields regardless of `*all`. |
| `jira_get_issue(fields='*all')` null handling | Even after the sentinel fix, `models/jira/issue.py` `to_simplified_dict()` emits all stored custom fields including unset ones as `{"value": null}` dicts. The final `v is not None` filter does not remove these because the wrapper dict is not `None`. On Jira instances with 2000+ custom field definitions, a single `*all` call floods LLM context with thousands of null entries. |
| Unqualified `jira_get_issue(issue_key)` | `DEFAULT_READ_JIRA_FIELDS` (`jira/constants.py` line 205) is a fixed 10-field "essential only" set (upstream design): `summary, description, status, assignee, reporter, labels, priority, created, updated, issuetype`. Relationship and resolution data require explicit `fields` parameters. |
| `_get_cloud_id()` (`utils/oauth.py` line 306) | Line 328 always takes `resources[0]["id"]` from the accessible-resources response. Correct only when the token has exactly one accessible resource, or the configured site happens to sort first |
| Post-auth token flow | No validation step exists anywhere between token exchange and caching. A wrong-site token is accepted, cached, and used until a downstream API call fails or returns unexpectedly empty data |

**Core pain points:**
1. `fields='*all'` is a contract violation — the tool's own docstring says "all fields (including custom fields)," but the code silently substitutes the 10-field default
2. Even after fixing the `*all` sentinel, the response processing emits all custom field definitions as `{"value": null}` — making `*all` unusable on large Jira instances without a null filter
3. Cloud site selection for multi-instance tokens is non-deterministic with respect to the deployment's configured site
4. There is no actionable failure mode when a token doesn't cover the configured site — failures surface later, indirectly, and without diagnosis

---

## 3. Proposed System ("To-Be")

The fix set is intentionally narrow and surgical, scoped to three source files, to stay easy to reconcile with a future upstream PR to `sooperset/mcp-atlassian`.

For field completeness: the `*all` branch in `jira/issues.py` is corrected to pass the `*all` sentinel directly to the Jira REST API (not expand it into the 10-field default). A null-value filter is added to `models/jira/issue.py`'s `to_simplified_dict()` so that when `requested_fields == "*all"`, only custom fields with non-null, non-empty values are emitted — preventing thousands of `{"value": null}` entries from reaching LLM context on instances with many defined custom fields. `DEFAULT_READ_JIRA_FIELDS` stays at the upstream's 10-field "essential only" set; the existing `jira_search_fields` tool is the field-discovery mechanism for callers that need relationship or custom field data.

For multi-instance OAuth: `_get_cloud_id()` is changed to search the accessible-resources list for an entry matching a configured `ATLASSIAN_OAUTH_CLOUD_ID` before falling back to `resources[0]`, preserving today's behavior exactly when no site is configured or only one resource is accessible. A new post-auth validation step runs immediately after token exchange completes; if a specific site is configured and the resolved token's accessible resources do not include it, the token is rejected — not cached — with an error message naming both the token's actual accessible site(s) and the required site.

Together, these changes mean a downstream caller like Archer's `ple-atlassian-mcp` gets contract-correct `*all` responses (populated fields only), and multi-instance users get deterministic site resolution with an immediate, actionable failure mode instead of silent wrong-site behavior discovered later.

---

## 4. User Roles & Profiles

| Role | Questions They Bring |
|---|---|
| Downstream MCP operator (e.g. `ple-atlassian-mcp` maintainer) | Does `fields='*all'` actually return all fields now? Do I still need to maintain a local patched field list? |
| Multi-instance engineer | Why did my session connect to the wrong Atlassian site, and how would I know when that's happened? |
| Fork maintainer (peakflames/mcp-atlassian) | Are these fixes minimal, fully tested, and safe to eventually cherry-pick into an upstream PR? |

---

## 5. Operational Scenarios

---

### Scenario 1: Full field discovery via `*all`

**Actor:** Downstream skill or MCP client performing full ticket discovery
**Trigger:** Caller invokes `jira_get_issue` with `fields="*all"` on an issue with populated custom fields
**Goal:** Retrieve every populated field, including custom fields, in a single call

**Steps:**
1. Client calls `jira_get_issue(issue_key="SWPRCO-2033", fields="*all")`
2. `servers/jira.py`'s `get_issue` tool passes `fields_param="*all"` into `jira/issues.py`'s `get_issue()`
3. Current (buggy) behavior: line 104's condition `fields_param == "*all"` is true, so execution falls into the "defaults" branch and sets `default_fields_list = list(DEFAULT_READ_JIRA_FIELDS)` at line 109
4. The underlying Jira REST call requests only the 10 default fields; populated custom fields such as `customfield_10334` are dropped from the response
5. Fixed behavior: the `*all` branch is separated from the "use defaults" branch — when `fields_param == "*all"`, the method requests the issue's full field catalog (all populated fields, standard and custom) instead of substituting `DEFAULT_READ_JIRA_FIELDS`
6. The response includes `customfield_10334` and any other populated custom field alongside the standard fields
7. A new/updated unit test in `tests/unit/jira/test_issues.py` asserts that `fields="*all"` produces a field set that is a strict superset of `DEFAULT_READ_JIRA_FIELDS` when custom fields are populated on the fixture issue, and is not equal to it

**Outcome:** `fields='*all'` behaves as documented — returns every populated field, not the same 10 as an unqualified call.

---

### Scenario 2: LLM-driven field discovery for relationship data

**Actor:** LLM assistant (e.g., Archer Atlassian AI Assist via `ple-atlassian-mcp`)
**Trigger:** User asks "which other tickets are linked to FCSW-54698?"
**Goal:** Retrieve issue link data without knowing the field ID in advance

**Steps:**
1. LLM receives the user's question and determines it needs `issuelinks` data
2. LLM calls `jira_search_fields(keyword="issuelinks", limit=5)` to confirm the field name and ID
3. `jira_search_fields` returns a list of matching field definitions including `{"id": "issuelinks", "name": "Linked Issues", "type": "array"}`
4. LLM calls `jira_get_issue(issue_key="FCSW-54698", fields="summary,status,issuelinks")`
5. `jira/issues.py` passes `fields="summary,status,issuelinks"` to the Jira REST API — not the default field set
6. The response includes `issuelinks` with the linked issue keys, directions, and link types
7. LLM formulates its answer from the explicit field data

**Outcome:** The LLM gets exactly the field data it needs for the user's question; the default 10-field response is not used for relationship queries. This pattern is more token-efficient than a broad default for callers that don't need relationship data.

---

### Scenario 3: Correct cloud site selection for a multi-instance token

**Actor:** Engineer with access to multiple Atlassian Cloud sites
**Trigger:** OAuth token exchange completes; `_get_cloud_id()` runs against a token whose accessible-resources response lists a non-configured site first
**Goal:** The server resolves to the site configured via `ATLASSIAN_OAUTH_CLOUD_ID` regardless of list order

**Steps:**
1. `_get_cloud_id()` in `utils/oauth.py` calls the accessible-resources endpoint and receives, e.g., `[{"id": "partner-site-id", ...}, {"id": "8b7be5e1-e593-4e28-b67d-2a22bd5a2e6a", ...}]`, where the second entry matches the deployment's configured `ATLASSIAN_OAUTH_CLOUD_ID`
2. Current (buggy) behavior: line 328 unconditionally sets `self.cloud_id = resources[0]["id"]`, selecting the partner site
3. Fixed behavior: when a configured cloud ID is available, `_get_cloud_id()` searches `resources` for an entry whose `id` matches it and uses that instead of defaulting to index 0
4. When no configured cloud ID is available (single-instance deployments), behavior is unchanged — falls back to `resources[0]["id"]`
5. Unit test: token with 2+ accessible resources where the configured site is not first in the list; asserts `self.cloud_id` equals the configured site's id, not `resources[0]`'s

**Outcome:** The resolved cloud ID exactly matches the configured site whenever a match exists in the token's accessible resources, regardless of list order.

---

### Scenario 4: Wrong-site token rejected with an actionable error

**Actor:** Same class of multi-instance engineer, this time authenticating with an account that has no access at all to the configured site
**Trigger:** Post-auth validation step runs immediately after token exchange completes
**Goal:** Get an immediate, actionable error instead of a token that is cached and fails mysteriously later

**Steps:**
1. OAuth flow completes; the accessible-resources response for this token contains only sites other than the configured `ATLASSIAN_OAUTH_CLOUD_ID`
2. A new validation step, run immediately after `_get_cloud_id()` resolution, checks whether the configured site is present among `resources`
3. If not present, the method raises an error naming both the token's actual accessible site(s) (by id/name) and the required configured site
4. The token is not cached or persisted when validation fails
5. Unit test: token whose resources list entirely excludes the configured site; asserts the validation raises with both site identifiers present in the error message and that no token is cached as a result
6. Existing single-resource-token tests continue to pass unchanged — no regression for single-instance deployments with no configured cloud ID

**Outcome:** The engineer gets an immediate, actionable message naming both sites instead of a silent failure surfacing later; no wrong-site token is ever cached.

---

## 6. System Interfaces & Data Flows

| Data Source | Interface | Freshness | Notes |
|---|---|---|---|
| Atlassian Cloud accessible-resources endpoint | `GET https://api.atlassian.com/oauth/token/accessible-resources` | Per OAuth flow | Source of truth for which sites a token can access; consumed by `_get_cloud_id()` in `utils/oauth.py` |
| Jira REST API `fields` parameter | `jira_get_issue`, `jira_search` | Real-time | `DEFAULT_READ_JIRA_FIELDS` and the `*all` branch both control what is requested here |

**Data flow — cloud ID resolution (fixed):**
```
OAuth token exchange completes
    → _get_cloud_id() calls accessible-resources endpoint
    → [FIXED] search resources for an entry matching configured ATLASSIAN_OAUTH_CLOUD_ID
    → if found: self.cloud_id = matching resource's id
    → [NEW] if a site IS configured but not found among resources: raise actionable
      dual-site error, do not cache token
    → if no site is configured: fall back to resources[0]["id"] (unchanged single-instance default)
```

**Data flow — field resolution (fixed):**
```
jira_get_issue(fields=...) / jira_search(fields=...)
    → fields omitted → fields_param = DEFAULT_READ_JIRA_FIELDS (10 fields, aligned with upstream)
    → fields == "*all" → [FIXED] send "*all" sentinel to Jira REST API
        → Jira returns all defined fields (including nulls for unset custom fields)
        → [NEW] to_simplified_dict() filters custom fields where value is null or []
        → Only populated custom fields reach LLM context
    → fields == explicit list → unchanged, requests exactly what was asked for

LLM-driven field selection pattern:
    jira_search_fields(keyword="...") → returns field definitions with IDs
    → LLM selects field IDs relevant to user's question
    → jira_get_issue(fields="summary,status,<field_id>,...") → targeted response
```

---

## 7. Functional Summary

| Feature | Description |
|---|---|
| Fixed `*all` sentinel | `fields='*all'` sends `*all` to the Jira REST API instead of silently substituting the 10-field default set |
| Null-safe `*all` output | When `*all` is requested, custom fields with null or empty values are filtered from the response — only populated custom fields reach LLM context |
| Upstream-aligned default field set | `DEFAULT_READ_JIRA_FIELDS` stays at the upstream's 10-field "essential only" set; `jira_search_fields` is the field-discovery mechanism for callers needing additional fields |
| Configured-site cloud ID resolution | `_get_cloud_id()` matches `ATLASSIAN_OAUTH_CLOUD_ID` when configured, before falling back to `resources[0]` |
| Post-auth site validation | A wrong-site token is rejected before caching, with an error naming both the token's actual site(s) and the required site |

---

## 8. Operational Constraints & Assumptions

| Constraint | Detail |
|---|---|
| Cloud-only fix | The Data Center OAuth path (`is_data_center`) is unaffected — `_get_cloud_id()` returns early for DC before reaching the cloud-ID logic |
| No new environment variables | Relies entirely on the existing `ATLASSIAN_OAUTH_CLOUD_ID` |
| Backward compatible | Single-resource-token and unconfigured-cloud-id behavior is unchanged by design |
| Downstream dependency | `ple-atlassian-mcp` must bump its `FROM peakflames/mcp-atlassian:` tag to pick up these fixes before its 5 deferred TOR IDs can be verified closed |
| Branch context | This work lands on `docs/atlassian-cloud-oauth-proxy`, which also carries unrelated in-flight work (per-project/per-space access control, OAuth proxy deployment guide) merged separately toward `peakflames/main` |

---

## 9. Glossary

| Term | Definition |
|---|---|
| `DEFAULT_READ_JIRA_FIELDS` | Python constant in `jira/constants.py` listing the 10 "essential only" fields returned when no explicit `fields` parameter is given — aligned with upstream `sooperset/mcp-atlassian` |
| `jira_search_fields` | Existing MCP tool for discovering Jira field definitions by keyword; provides field IDs so callers can request specific fields without knowing IDs in advance |
| `_get_cloud_id()` | Method in `utils/oauth.py` that resolves which Atlassian Cloud site a token's API calls should target |
| accessible-resources | Atlassian OAuth endpoint (`api.atlassian.com/oauth/token/accessible-resources`) listing every Cloud site a given token can reach |
| `ATLASSIAN_OAUTH_CLOUD_ID` | Environment variable naming the specific Cloud site a deployment should use; already consumed downstream in `ple-atlassian-mcp`'s Dockerfile |
| TOR ID | Test Objective Requirement identifier from Archer's peak-workflow requirements baseline; this project resolves TOR-01-MDNHg8G, TOR-01-JKVYLpG, TOR-03-J54hatP, and TOR-03-quJs6mG from `ple-atlassian-mcp`, and unblocks TOR-03-XnkkkuJ which depends on all four |
