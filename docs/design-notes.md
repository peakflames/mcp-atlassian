# Design Notes

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

**Evidence**: Epic XG1cmts handoff — all 18 field-related constants and behaviors are isolated in `jira/constants.py` and `jira/issues.py`, with clear test boundaries in `tests/unit/jira/test_constants.py` and `tests/unit/jira/test_issues.py`.

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

**Trade-off**: The response may be large and slow to parse. Mitigated by optional response filtering in preprocessing and encouraging default field requests for most use cases.

**Evidence**: Epic XG1cmts handoff — fixed `jira_get_issue` to pass `["*all"]` directly (lines 104–110 in `src/mcp_atlassian/jira/issues.py`). Tests verify this behavior in `tests/unit/jira/test_issues.py:1800`.

---

### 4. DEFAULT_READ_JIRA_FIELDS as a Curated Set

**Decision**: Maintain a `DEFAULT_READ_JIRA_FIELDS` constant (18 fields) that covers the most commonly needed fields and is used as the default when no explicit fields are requested.

**Rationale**:
- **Performance**: Reduces response size and parsing time for typical queries (issues, search, board items).
- **Discoverability**: Explicit set makes it clear what fields an LLM will receive by default.
- **Relationship coverage**: Includes relationship fields (`issuelinks`, `subtasks`, `parent`) and resolution fields (`resolution`, `resolutiondate`) that are essential for understanding issue context.
- **Consistency**: All callers (`jira_get_issue`, `jira_search`, `jira_get_board_issues`) inherit the same default set.

**Trade-off**: If a field is not in the default set and the user doesn't request it explicitly, it will not be returned. Mitigated by documenting the default set and allowing `fields='*all'` override.

**Composition** (18 fields):
- Core: `summary`, `description`, `status`, `issuetype`, `priority`, `created`, `updated`
- People: `assignee`, `reporter`
- Relationships: `issuelinks`, `subtasks`, `parent`, `components`, `labels`
- Versions: `fixVersions`, `attachment`
- Resolution: `resolution`, `resolutiondate`

**Evidence**: Epic XG1cmts handoff — expanded from 10 to 18 fields. Tests verify count in `tests/unit/jira/test_constants.py:55`.

---

### 5. Configuration via Environment Variables and Dataclasses

**Decision**: Use Pydantic dataclasses (`JiraConfig`, `ConfluenceConfig`) with a `from_env()` factory method to load all configuration from environment variables at startup.

**Rationale**:
- **12-Factor compliance**: Externalizes secrets and deployment-specific settings from code.
- **Validation**: Pydantic validates types, required fields, and constraints (e.g., URL formats) at startup, failing fast.
- **Type safety**: Dataclass type hints enable IDE autocomplete and mypy checking.
- **Flexibility**: Supports multiple authentication methods (Basic, PAT, OAuth) via conditional field presence.

**Trade-off**: Env vars are untyped strings until Pydantic parses them. Mitigated by comprehensive validation and `.env.example` documentation.

**Evidence**: `src/mcp_atlassian/jira/config.py` and `src/mcp_atlassian/confluence/config.py` define configs with `from_env()` methods.

---

### 6. Pydantic v2 for All Data Models

**Decision**: Use Pydantic v2 (not v1) for all data models extending `ApiModel` base class with `from_api_response()` and `to_simplified_dict()` methods.

**Rationale**:
- **Modern**: Pydantic v2 has better performance, validation composability, and JSON schema support.
- **Consistency**: All models use the same serialization/deserialization pattern.
- **LLM-friendly**: The `to_simplified_dict()` method strips internal fields and complex nested structures, producing clean dicts for LLM consumption.
- **Field documentation**: Pydantic's `Field()` allows per-field documentation that aids debugging and LLM context.

**Trade-off**: v2 has breaking changes from v1. Mitigated by managing dependencies carefully and using comprehensive type hints.

**Evidence**: All models in `src/mcp_atlassian/models/` extend `ApiModel` and implement both factory methods.

---

### 7. TTLCache for Short-Lived Response Caching

**Decision**: Implement response caching via TTLCache (from cachetools) with a configurable TTL (default 5 minutes) in the `CachingMixin`.

**Rationale**:
- **Reduce API calls**: Repeated queries within the TTL window reuse cached responses, reducing Jira/Confluence API load and improving latency.
- **Cost savings**: Fewer API calls → lower API token usage and quota.
- **Stale tolerance**: 5-minute default TTL balances freshness with cache hit rate for most workflows.
- **Configurable**: TTL can be adjusted via env var (`JIRA_CACHE_TTL_SECONDS`, etc.) for different use cases.

**Trade-off**: Cached data can be stale. For real-time consistency, the user can disable caching or use a short TTL.

**Implementation**: Cache key is derived from method name + arguments (e.g., `get_issue(PROJECT-123)` → `get_issue|PROJECT-123`).

**Evidence**: `src/mcp_atlassian/jira/` mixins integrate with `CachingMixin` via inheritance.

---

### 8. Content Conversion: ADF → Markdown, Storage XML → Markdown

**Decision**: Convert Atlassian Document Format (ADF) and Confluence Storage XML to Markdown for LLM readability.

**Rationale**:
- **Clarity**: Markdown is human-readable and widely understood by LLMs.
- **Consistency**: Both Jira (ADF) and Confluence (Storage XML) are converted to the same format, simplifying downstream processing.
- **Compatibility**: Markdown is a standard, stable format unlikely to change.

**Trade-off**: Conversion can lose fidelity for complex ADF structures (embedded macros, custom formatting). Mitigated by preserving structure where possible and documenting limitations.

**Implementation**: Uses `markdownify` library for HTML-to-Markdown conversion after ADF/Storage XML is parsed.

**Evidence**: `src/mcp_atlassian/preprocessing/jira.py` and `src/mcp_atlassian/preprocessing/confluence.py`.

---

### 9. Project-Level Access Control

**Decision**: Implement access control at the project level with two modes: whitelist (allow specific projects) and blocklist (block or mark specific projects as read-only).

**Rationale**:
- **Multi-tenant safety**: When a single MCP server instance serves multiple tenants or users, project filtering prevents cross-tenant data leakage.
- **Flexibility**: Whitelist mode is safe by default (deny all, allow specific); blocklist mode is permissive (allow all, restrict specific).
- **Early validation**: Access checks run **before** API calls, preventing unnecessary Atlassian API calls to unauthorized projects.

**Trade-off**: Filtering is project-specific; cross-project queries (e.g., via JQL) are not automatically filtered. Mitigated by documenting the limitation and relying on Jira/Confluence permissions as the primary control.

**Implementation**: `utils/access_control.py` provides `check_jira_project_access()` function called in issue-related tools.

**Evidence**: `src/mcp_atlassian/jira/issues.py:88–90` checks access before calling API.

---

### 10. Read-Only Mode at Server Level

**Decision**: Implement a global `READ_ONLY_MODE` flag that blocks all write tools before they execute.

**Rationale**:
- **Safety**: Prevents accidental mutations in read-only environments (e.g., demos, audits, non-prod).
- **Simplicity**: Single flag controls all write operations without per-tool configuration.
- **Fail-fast**: Tools that attempt writes raise an error immediately, providing clear feedback.

**Trade-off**: All write tools must be wrapped in the same check, requiring discipline. Mitigated by centralizing the check in `servers/main.py` lifespan hook.

**Evidence**: Read-only mode blocks all write tool schemas at server startup if `READ_ONLY_MODE=true`.

---

## Known Issues and Deferred Work

### Pre-existing Issues

1. **TTLCache type argument warning** (`servers/main.py:363`):
   - `TTLCache` is instantiated with 2 type arguments but expects 3.
   - **Fix**: Add `float` as the third type argument or suppress with `# type: ignore[type-arg]`.
   - **Priority**: Low (does not affect runtime behavior).

2. **Weak test assertions in test_search.py**:
   - `test_search.py:1323, :1363` contain `or True` guards that render secondary sub-assertions permanently passing.
   - **Fix**: Remove the `or True` guards and ensure assertions are meaningful.
   - **Priority**: Low (primary assertions are correct; this is a test quality issue).

### Deferred Work

1. **Upstream contribution**:
   - File upstream issues against `sooperset/mcp-atlassian` for:
     - The `*all` field sentinel bug (missing in older versions).
     - The missing default fields (field expansion incomplete).
   - Create `contrib/` branch off `upstream/main` and open a PR.
   - **Requires**: `gh` CLI authentication to the upstream repo.
   - **Status**: Not started.

2. **Multi-tenant OAuth**:
   - Epic 1SVldWi (Not Started) will implement multi-tenant OAuth support.
   - Requires enhanced session storage and tenant-aware token refresh.

3. **Additional field expansion**:
   - As more features are built, additional fields (e.g., `securityLevel`, `customfield_*`) may be added to `DEFAULT_READ_JIRA_FIELDS`.
   - Should be done judiciously to avoid bloating default responses.

---

## Architecture Principles

### 1. Single Responsibility

Each mixin, model, and utility module handles one domain. This makes tests focused and code changes localized.

### 2. Configuration Over Convention

Settings are explicit (env vars, dataclass fields) rather than implicit. This reduces surprises and aids debugging.

### 3. Type Safety

All functions have type hints. Mypy runs in strict mode in CI/pre-commit. This catches errors early and aids IDE support.

### 4. Fail Fast

Validation and access checks run early (at config load and before API calls). Invalid configs are rejected at startup, not at runtime.

### 5. Async Throughout

All I/O operations use async/await. The Trio event loop manages concurrency, allowing multiple tools to run concurrently within the server.

### 6. LLM-Focused Simplification

Models' `to_simplified_dict()` methods remove internal fields and flatten deeply nested structures. This produces clean, readable dicts for LLM consumption.

---

## Future Considerations

### OAuth 2.0 Multi-Tenant

As multi-tenant OAuth is implemented (Epic 1SVldWi), additional consideration is needed for:
- Token refresh and expiration handling
- Session isolation between tenants
- Secure storage of refresh tokens

### Rate Limiting and Quota Management

The server does not currently implement rate-limit tracking or quota management. Future enhancements could:
- Track remaining API calls via Atlassian headers
- Implement backoff strategies when rate limits are approached
- Expose quota metrics in health-check endpoints

### Extended Content Preprocessing

Current preprocessing handles ADF and Storage XML. Future enhancements could:
- Preserve embedded images and attachments as URLs
- Handle complex macros with fallback text
- Implement custom rendering for Jira Forms (proforma questionnaires)

### Real-Time Sync

The server is currently synchronous (request-response). Future enhancements could:
- Stream issue and page updates via webhooks
- Implement subscriptions for real-time field updates
- Add change notification aggregation
