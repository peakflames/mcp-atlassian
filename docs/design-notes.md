# Design Notes

> **Last refreshed:** 2026-07-14  
> **Basis:** Epics XG1cmts + Rm1iZNA handoffs  
> **Audience:** Developers, contributors, maintainers

## Overview

This document records key architectural decisions, rationale, and tradeoffs made during the implementation of MCP Atlassian.

## Design Decisions

### 1. Mixin Composition Pattern for Client Classes

**Decision**: Use Python mixin classes to organize Jira and Confluence client functionality into separate modules while maintaining a single cohesive client class through transitive inheritance.

**Rationale**:
- **Modularity**: Each mixin handles a specific domain (issues, search, fields, SLA, etc.), making the codebase easier to navigate and maintain.
- **Single responsibility**: Each file (~300–500 LOC) focuses on one operational domain.
- **Discoverability**: Developers can quickly find the method they need by looking at mixin names.
- **Testing**: Mixins can be unit-tested in isolation by creating test clients that compose only the relevant mixins.

**Trade-off**: Transitive inheritance can obscure method resolution order (MRO) for complex inheritance chains. Mitigated by explicit protocol definitions (`protocols.py`) that document the expected interface.

---

### 2. FastMCP Server with Lifespan Dependency Injection

**Decision**: Use FastMCP's lifespan container to instantiate `JiraFetcher` and `ConfluenceFetcher` once at startup, then inject via context into every tool handler.

**Rationale**:
- **Single instance**: Reduces memory footprint and allows session/cache state to be shared across tools within a single server process.
- **Startup validation**: OAuth, connection parameters, and SSL certificates are validated once during lifespan, failing fast if config is invalid.
- **Async-safe**: Lifespan runs in the event loop, ensuring all async initialization (OAuth token exchange, connection pooling) completes before tools are invoked.
- **Context isolation**: Each request sees a clean dependency graph via `get_jira_fetcher(ctx)` and `get_confluence_fetcher(ctx)`, enabling easy mocking in tests.

**Trade-off**: If a client becomes corrupted during execution, all tools fail until the server restarts. Mitigated by comprehensive error handling in each tool and health-check endpoints (if deployed as HTTP).

**Evidence**: `src/mcp_atlassian/servers/main.py` lifespan hook instantiates clients; `src/mcp_atlassian/servers/dependencies.py` provides injection helpers.

---

### 3. `*all` Sentinel Over Explicit Field Expansion

**Decision**: When a user requests `fields='*all'`, pass the literal string `["*all"]` to the Jira API instead of expanding it into a list of known field names.

**Rationale**:
- **Future-proof**: If Jira adds new fields to the instance (custom fields, new standard fields), the `*all` sentinel will automatically include them without requiring code changes.
- **Correctness**: The Jira REST API documents `*all` as the proper way to request the complete field set; expanding it violates the API contract.
- **Custom fields**: Many Jira instances have custom fields that are not known in advance. Only the `*all` sentinel guarantees they will be included.
- **Simplicity**: No field enumeration logic needed in the tool layer; Jira handles it.

**Trade-off**: The response may be large and slow to parse. Mitigated by the null-filter (Decision #4) and optional response filtering in preprocessing.

**Evidence**: Epic XG1cmts handoff — fixed `jira_get_issue` to pass `["*all"]` directly. Tests verify this behavior in `tests/unit/jira/test_issues.py`.

---

### 4. Scoped Null Filter for `*all` Responses

**Decision**: When `requested_fields == "*all"`, custom fields with null or empty-list values are **excluded** from `to_simplified_dict()` output. Explicitly-requested fields always return their value, including null.

**Rationale**:
- Jira instances with 2000+ defined custom fields (like Archer) return massive responses with mostly null values when `*all` is used. Including these nulls floods the LLM context window without adding information.
- Callers who explicitly request a specific field by name still receive null values, preserving the documented contract: "when you ask for a specific field, you get it."

**Implementation** — `src/mcp_atlassian/models/jira/issue.py:627–630`:

```python
if self.requested_fields == "*all":
    for internal_id, field_data_obj in self.custom_fields.items():
        processed_value = self._process_custom_field_value(field_data_obj.get("value"))
        if processed_value is None or (isinstance(processed_value, list) and not processed_value):
            continue  # skip null and empty-list fields
        result[internal_id] = {"value": processed_value}
elif isinstance(self.requested_fields, list):
    # explicit field requests include null values
```

**Evidence**: Epic Rm1iZNA handoff — TOR-01-twYUvG9 regression guard at `tests/unit/jira/test_issues.py::TestIssuesMixin::test_get_issue_all_fields_excludes_null_custom_fields`.

---

### 5. DEFAULT_READ_JIRA_FIELDS as a Curated 10-Field Set

**Decision**: Maintain a `DEFAULT_READ_JIRA_FIELDS` constant with exactly 10 fields, aligned with the upstream sooperset/mcp-atlassian project.

**Rationale**:
- **Performance**: Reduces response size and parsing time for typical queries.
- **Upstream alignment**: Matches the upstream project's contract; callers needing relationship or custom field data use `jira_search_fields` to discover field IDs and pass them explicitly.
- **Discoverability**: Explicit minimal set makes it clear what fields an LLM receives by default.

**Fields (10):** `summary`, `description`, `status`, `assignee`, `reporter`, `labels`, `priority`, `created`, `updated`, `issuetype`

**Evidence**: `tests/unit/jira/test_constants.py::TestDefaultReadJiraFields` — 4 tests confirm the set is exactly these 10 fields, length 10, no extras.

---

### 6. Configuration via Environment Variables and Dataclasses

**Decision**: Use Pydantic dataclasses (`JiraConfig`, `ConfluenceConfig`) with a `from_env()` factory method to load all configuration from environment variables at startup.

**Rationale**:
- **12-Factor compliance**: Externalizes secrets and deployment-specific settings from code.
- **Validation**: Pydantic validates types, required fields, and constraints (e.g., URL formats) at startup, failing fast.
- **Type safety**: Dataclass type hints enable IDE autocomplete and mypy checking.
- **Flexibility**: Supports multiple authentication methods (Basic, PAT, OAuth) via conditional field presence.

**Evidence**: `src/mcp_atlassian/jira/config.py` and `src/mcp_atlassian/confluence/config.py`.

---

### 7. Pydantic v2 for All Data Models

**Decision**: Use Pydantic v2 (not v1) for all data models extending `ApiModel` base class with `from_api_response()` and `to_simplified_dict()` methods.

**Rationale**:
- **Modern**: Pydantic v2 has better performance, validation composability, and JSON schema support.
- **Consistency**: All models use the same serialization/deserialization pattern.
- **LLM-friendly**: The `to_simplified_dict()` method strips internal fields and complex nested structures, producing clean dicts for LLM consumption.

**Evidence**: All models in `src/mcp_atlassian/models/` extend `ApiModel` and implement both factory methods.

---

### 8. TTLCache for Short-Lived Response Caching

**Decision**: Implement response caching via TTLCache (from cachetools) with a configurable TTL (default 5 minutes) in the `CachingMixin`.

**Rationale**:
- **Reduce API calls**: Repeated queries within the TTL window reuse cached responses, reducing Atlassian API load.
- **Configurable**: TTL can be adjusted via env var (`JIRA_CACHE_TTL_SECONDS`, etc.) for different use cases.

**Cache key**: Method name + arguments (e.g., `get_issue(PROJECT-123)` → `get_issue|PROJECT-123`).

---

### 9. Content Conversion: ADF → Markdown, Storage XML → Markdown

**Decision**: Convert Atlassian Document Format (ADF) and Confluence Storage XML to Markdown for LLM readability.

**Rationale**:
- **Clarity**: Markdown is human-readable and widely understood by LLMs.
- **Consistency**: Both Jira (ADF) and Confluence (Storage XML) are converted to the same format.

**Trade-off**: Conversion can lose fidelity for complex ADF structures (embedded macros, custom formatting).

**Evidence**: `src/mcp_atlassian/preprocessing/jira.py` and `src/mcp_atlassian/preprocessing/confluence.py`.

---

### 10. Project-Level Access Control

**Decision**: Implement access control at the project level with two modes: whitelist (allow specific projects) and blocklist (block or mark specific projects as read-only).

**Rationale**:
- **Multi-tenant safety**: Prevents cross-tenant data leakage when a single MCP server instance serves multiple users.
- **Early validation**: Access checks run **before** API calls, preventing unnecessary Atlassian API calls to unauthorized projects.

**Implementation**: `utils/access_control.py` provides `check_jira_project_access()` called in issue-related tools.

---

### 11. Read-Only Mode at Server Level

**Decision**: Implement a global `READ_ONLY_MODE` flag that blocks all write tools before they execute.

**Rationale**:
- **Safety**: Prevents accidental mutations in read-only environments (e.g., demos, audits, non-prod).
- **Simplicity**: Single flag controls all write operations without per-tool configuration.

---

## Known Issues

### Pre-existing Issues (not introduced by recent epics)

- **Unreachable code warning:** `issue.py:260` — mypy reports `Statement is unreachable [unreachable]` (no behavior impact, low priority)
- **Windows path test failure:** `tests/unit/jira/test_attachments.py` — `/tmp/` vs `C:\tmp\` mismatch on Windows runners (isolated to attachment test)
- **TTLCache type argument warning:** `servers/main.py` — `TTLCache` instantiated with 2 type arguments where 3 are expected (no runtime impact)
- **Weak test assertions:** `test_search.py` — some secondary sub-assertions use `or True` guards, rendering them always-passing (primary assertions are correct)

### Deferred Work

- **Upstream contribution:** File upstream issues against `sooperset/mcp-atlassian` for the `*all` field sentinel fix and null-filter. Create `contrib/` branch off `upstream/main`.
- **Multi-tenant OAuth:** Epic 1SVldWi (Not Started) — requires enhanced session storage and tenant-aware token refresh.

---

## Testing Strategy

**Unit tests** (fast, isolated):
- Model serialization (`to_simplified_dict()` output)
- Field filtering logic (null/empty-list exclusion in `*all` mode)
- Field constant verification (exact 10-field set)

**Integration tests** (require real Jira/Confluence):
- End-to-end OAuth flow
- Per-tenant client isolation
- API compatibility (Cloud vs Server/DC)

**Pre-commit hooks:**
- Ruff linting (88-char line length)
- mypy type checking (strict mode on `src/`, relaxed on `tests/`)
- Import sorting

---

## Architectural Invariants

1. **Null filtering is read-only:** `to_simplified_dict()` filters; request handling does not mutate API response payloads.
2. **Explicit requests bypass filtering:** If a caller asks for a specific field, they get its value, even if null.
3. **Cloud and Server/DC branches are stable:** Code uses `is_cloud` to partition logic; both paths are tested.
4. **Tool naming is consistent:** `{service}_{action}_{target}` pattern held across all tools.
5. **Models are immutable in transit:** Pydantic models are frozen at the API boundary; no in-flight mutation.

---

## Future Considerations

1. **Performance optimization:** Cache field definitions per instance to reduce descriptor API calls.
2. **Rate limit handling:** Backoff strategy for high-volume search and bulk operations.
3. **Workspace federation:** Support for Jira Service Management, Portfolio, and Automation Server.
4. **Extended content preprocessing:** Preserve embedded images, handle complex macros with fallback text.
5. **Real-time sync:** Stream issue and page updates via webhooks.

---

*Last reviewed: 2026-07-14 (Epic Rm1iZNA completion)*  
*Next review: After Epic 1SVldWi completion (TOR-02 Multi-Instance OAuth)*
