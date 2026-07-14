# mcp-atlassian Field Completeness & Multi-Instance Auth Patches — Product Vision & Brief

**Document Version:** 2.0
**Date:** 2026-07-13
**Status:** Draft

---

## 1. Product Name

**mcp-atlassian Field Completeness & Multi-Instance Auth Patches**
*Closing the field-visibility and multi-site OAuth gaps in the peakflames/mcp-atlassian fork that block Archer's Atlassian AI Assist initiative downstream*

---

## 2. Problem Statement

`peakflames/mcp-atlassian` is the base image for Archer's Atlassian MCP server (`ple-atlassian-mcp`). That companion project's discovery process (Archer Atlassian AI Assist, `docs/product-vision-planning/` in `ple-atlassian-mcp`) identified several completeness and correctness gaps in the MCP tool surface. Most were closable with Dockerfile `ENV` changes or Atlassian Developer Console configuration. Five were not — they live in this fork's Python source and require a code change here before the downstream project can close its own requirements baseline.

Two of the five are field-completeness defects, both confirmed by direct source inspection on 2026-07-08 and revised after maintainer feedback on 2026-07-13:

1. **The `*all` formatter contract violation.** `servers/jira.py`'s `get_issue` tool documents `fields='*all'` as meaning "all fields (including custom fields)." But `jira/issues.py` line 104 reads `if fields_param == "*all" or fields_set == DEFAULT_READ_JIRA_FIELDS:` — it treats an explicit request for every field as identical to an unqualified default request, then falls through to `list(DEFAULT_READ_JIRA_FIELDS)` at line 109. A caller asking for everything gets the same 10 fields as a caller who asked for nothing. This matches the upstream defect exactly.
2. **Null custom fields in `*all` responses.** After the sentinel fix (sending `*all` to the Jira REST API), Jira returns *all defined fields* — including unset custom fields as `null`. `from_api_response` stores every `customfield_*` key regardless of value, and `to_simplified_dict()` emits all of them as `{"value": null}` dicts. The final null filter (`v is not None`) does not catch these because the wrapper dict is not `None`. For Jira instances with thousands of defined custom fields (Archer has 2000+), a `*all` call would dump thousands of `{"customfield_XXXXX": {"value": null}}` entries into the LLM context, making `*all` actively harmful at scale.
3. **The default field set alignment.** `jira/constants.py` line 205 defines `DEFAULT_READ_JIRA_FIELDS` as 10 fields — the upstream's deliberate "essential fields only" default. An earlier iteration of this plan attempted to expand it to 18 fields (adding relationship/resolution fields). After maintainer feedback and upstream reference review, that approach is reversed: the default stays at 10 fields, aligned with upstream. Callers needing relationship data use explicit `fields` parameters; the existing `jira_search_fields` tool provides field discovery so an LLM assistant can identify the right field IDs before requesting them.

The other two are multi-instance OAuth defects, both in `utils/oauth.py`:

3. **Non-deterministic cloud site selection.** `_get_cloud_id()` (line 306) unconditionally sets `self.cloud_id = resources[0]["id"]` (line 328) from the token's accessible-resources list. For a user with access to more than one Atlassian Cloud site — for example, an internal Archer instance and an external partner or customer instance — whichever site the API happens to return first wins, regardless of which site the deployment is actually configured for via `ATLASSIAN_OAUTH_CLOUD_ID`.
4. **No post-auth validation.** There is no step anywhere in the OAuth flow that checks whether the resolved token's accessible resources actually include the configured site. A multi-instance user who authenticates against the wrong account gets a token that is cached and used until a downstream API call fails or returns unexpectedly empty data, with no diagnostic pointing at the real cause.

**Pain points:**
- `fields='*all'` silently returns the same 10 fields as no `fields` parameter at all, contradicting its own documented behavior
- `*all` without null filtering on large Jira instances (Archer has 2000+ custom fields) floods LLM context with thousands of `{"value": null}` entries, making the feature actively harmful at scale
- `_get_cloud_id()` has no way to honor a configured cloud site when it isn't first in the token's accessible-resources list
- No post-auth check exists to catch and reject a wrong-site token before it is cached
- Five TOR requirements in `ple-atlassian-mcp`'s baseline — TOR-01-MDNHg8G, TOR-01-JKVYLpG, TOR-03-J54hatP, TOR-03-quJs6mG, and TOR-03-XnkkkuJ (which depends on the other four) — are deferred pending this work landing here

---

## 3. Target Users

| User Group | Primary Need |
|---|---|
| Downstream MCP server operators (e.g. Archer's `ple-atlassian-mcp`) | Correct, complete `jira_get_issue`/`jira_search` responses and reliable cloud-site resolution without maintaining local patches |
| Multi-instance Atlassian users | Deterministic connection to the configured cloud site, with a clear, actionable error when a cached token covers the wrong one |
| Fork maintainers (peakflames/mcp-atlassian) | Minimal, well-tested fixes that stay easy to reconcile with upstream `sooperset/mcp-atlassian` later |

---

## 4. Vision Statement

Make `jira_get_issue` and `jira_search`'s field behavior match their documented contract, and make multi-instance OAuth cloud-site resolution deterministic and self-diagnosing — so downstream deployments like Archer's `ple-atlassian-mcp` get complete data and safe multi-site behavior without needing to carry local patches.

---

## 5. Goals & Success Criteria (MVP)

| Goal | Success Criteria |
|---|---|
| Fix the `*all` formatter contract violation | `jira_get_issue(fields='*all')` sends `*all` to the Jira REST API rather than silently substituting the 10-field default set |
| Filter null custom fields from `*all` responses | When `fields='*all'` is used, custom fields with null values are excluded from the response — only populated custom fields appear in LLM context |
| Align default field set with upstream | `DEFAULT_READ_JIRA_FIELDS` stays at the upstream's 10-field "essential only" set; callers needing relationship data use `jira_search_fields` to discover field IDs and pass them explicitly |
| Deterministic cloud ID resolution | `_get_cloud_id()` selects the resource matching a configured `ATLASSIAN_OAUTH_CLOUD_ID` rather than defaulting to `resources[0]` |
| Post-auth site validation | A new validation step rejects a token whose accessible resources don't include the configured cloud site, with an error naming both the token's actual site(s) and the required site |
| Unblock the downstream TOR baseline | TOR-01-MDNHg8G, TOR-01-JKVYLpG, TOR-03-J54hatP, TOR-03-quJs6mG, and TOR-03-XnkkkuJ in `ple-atlassian-mcp`'s requirements baseline become satisfiable once this ships and the downstream image tag is bumped |

---

## 6. MVP Scope Summary

**JIRA Field Completeness** (`src/mcp_atlassian/jira/`, `src/mcp_atlassian/models/jira/`)
- Fix the `*all` branch in `issues.py`'s `get_issue` so it passes `*all` to the Jira REST API rather than falling back to the default set (sentinel fix)
- Add null-value filtering for custom fields in `to_simplified_dict()` — when `requested_fields == "*all"`, skip any custom field whose processed value is `None` or an empty list; this prevents thousands of null entries reaching the LLM context on instances with many defined custom fields
- Keep `DEFAULT_READ_JIRA_FIELDS` at the upstream's 10-field "essential only" set — no expansion; the existing `jira_search_fields` tool is the field-discovery mechanism for callers that need relationship or custom fields
- Add/update unit tests covering: `*all` sentinel reaches the API, null custom fields are filtered from `*all` output, populated custom fields appear in `*all` output, explicit field list behavior is unchanged

**Multi-Instance OAuth** (`src/mcp_atlassian/utils/oauth.py`)
- Fix `_get_cloud_id()` to prefer the resource matching a configured `ATLASSIAN_OAUTH_CLOUD_ID` over `resources[0]`
- Add a post-auth validation step that raises a clear, actionable error (naming both the actual and required sites) when the resolved token doesn't cover the configured site, and does not cache the token in that case
- Unit tests covering: single-resource token (unchanged behavior), multi-resource token with the configured site not first, multi-resource token missing the configured site entirely

**Cross-cutting Concerns**
- Source-only change — no `ENABLED_TOOLS` or Dockerfile-level config is affected
- Fixes must be minimal and surgical, kept easy to reconcile toward an eventual upstream PR to `sooperset/mcp-atlassian`
- Coordinate with `ple-atlassian-mcp`: once a fork release ships these fixes, that repo bumps its `FROM peakflames/mcp-atlassian:` tag in the Dockerfile and can close its 5 deferred TOR IDs

---

## 7. Out of Scope for MVP

- Upstreaming these fixes to `sooperset/mcp-atlassian` (tracked separately, following the pattern in `ple-atlassian-mcp`'s `upstream-workarounds.md`)
- Confluence-side field completeness work (no equivalent defect identified)
- The `read:devinfo:jira` OAuth scope requirement — that is Atlassian Developer Console configuration plus a Dockerfile `ENV` change, not a fork code change, and stays entirely in `ple-atlassian-mcp`
- The Data Center OAuth path — `is_data_center` short-circuits `_get_cloud_id()` before the cloud ID logic runs; this fix is Cloud-only
- The unrelated per-project/per-space access control and OAuth proxy deployment guide work already in flight on the `docs/atlassian-cloud-oauth-proxy` branch — separate concern, not touched by this effort

---

## 8. Key Business Scenarios

**Scenario 1: Full field discovery via `*all`**
A downstream skill performing full ticket discovery calls `jira_get_issue` with `fields='*all'` expecting every populated field, including custom fields. Today it silently gets the same 10-field default. After the fix, it gets the complete field set.

**Scenario 2: LLM-driven field discovery for relationship data**
An LLM assistant needs to answer "which other tickets are linked to PROJ-123?" It does not know the exact field ID for issue links. It first calls `jira_search_fields(keyword="issuelinks")` to confirm the field name, then calls `jira_get_issue(issue_key="PROJ-123", fields="summary,status,issuelinks")` with an explicit field list. The default 10-field response is not used for relationship queries — the LLM selects its own fields based on the user's question.

**Scenario 3: Correct site selection for a multi-instance token**
An engineer with access to more than one Atlassian Cloud site authenticates; the accessible-resources response happens to list a non-configured site first. The deployment is configured for a specific site via `ATLASSIAN_OAUTH_CLOUD_ID`. After the fix, `_get_cloud_id()` still resolves to the configured site.

**Scenario 4: Wrong-site token rejected with an actionable error**
The same class of multi-instance engineer authenticates with an account that has no access at all to the configured site. After the fix, a post-auth validation step rejects the token immediately, naming both the token's actual accessible site(s) and the required site, and the token is never cached.

---

## 9. Design Direction

- **Surgical, minimal diffs:** changes scoped narrowly to `jira/issues.py`, `models/jira/issue.py`, and `utils/oauth.py` — no broader refactors — to keep this easy to reconcile toward an eventual upstream PR
- **Align with upstream defaults:** `DEFAULT_READ_JIRA_FIELDS` stays at the upstream's 10-field "essential only" set; the maintainer's philosophy is that callers opt in to additional fields rather than receiving them by default
- **LLM-driven field selection via `jira_search_fields`:** the recommended pattern for an LLM assistant that needs relationship data — discover field IDs first, then request them explicitly; this is more token-efficient than a broad default for callers that don't need those fields
- **Null-safe `*all`:** `*all` responses include only populated custom fields; null and empty-list values are filtered before the response reaches LLM context
- **Backward compatible by default:** single-instance deployments (no `ATLASSIAN_OAUTH_CLOUD_ID` configured, or a token with exactly one accessible resource) see zero behavior change for OAuth
- **Actionable, dual-sided errors:** the wrong-site error names both the token's actual site and the required site explicitly — never a generic "authentication failed"
- **No new environment variables:** relies entirely on the existing `ATLASSIAN_OAUTH_CLOUD_ID`
- **Tests are the acceptance evidence:** every fix ships with a unit test that fails against the pre-fix code

---

## 10. Data Strategy

This effort changes request/response field shaping and OAuth token resolution logic only — no persistent data model changes. Field selection is computed per-request; cloud ID resolution and the new validation step are computed per-authentication-event. The existing token caching mechanism is unchanged; only the pre-cache validation gate is new, and it runs before any cache write.

---

## 11. Backlog / Future Vision

- Contribute these fixes upstream to `sooperset/mcp-atlassian` once stabilized here, following the fork's existing `contrib/` branch process (see `CLAUDE.md`)
- Extend the "configured cloud ID wins" resolution pattern to Confluence's OAuth config path, if an equivalent multi-site ambiguity exists there
- Evaluate whether Data Center OAuth needs an analogous post-auth validation step (currently out of scope — DC short-circuits before `_get_cloud_id()` runs)
- Consider exposing the `*all` field-catalog fetch as a first-class, documented, tested capability rather than a special case bolted onto the default-fields code path
