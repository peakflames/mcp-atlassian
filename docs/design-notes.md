# Design Notes — MCP Atlassian

> **Last refreshed:** 2026-07-14  
> **Basis:** Epic Rm1iZNA handoff  
> **Audience:** Developers, contributors, maintainers

---

## Design Decision Index

1. [Scoped Null Filter for Jira `*all` Requests](#1-scoped-null-filter-for-jira-all-requests)
2. [DEFAULT_READ_JIRA_FIELDS Upstream Alignment](#2-default_read_jira_fields-upstream-alignment)
3. [Mixin Composition Pattern](#3-mixin-composition-pattern)
4. [Dependency Injection via MCP Context](#4-dependency-injection-via-mcp-context)
5. [Pydantic v2 for All Models](#5-pydantic-v2-for-all-models)

---

## 1. Scoped Null Filter for Jira `*all` Requests

**Decision:** When a caller requests `fields='*all'` in Jira issue retrieval, custom fields with null or empty-list values are excluded from the response. This scoping is intentional and narrow.

**Rationale:**

Jira instances with 2000+ defined custom fields (like Archer) generate massive API responses (~1-2 MB per issue) with mostly null values. Including all those nulls in the response floods the LLM context window without adding information — an empty field is not actionable.

However, callers who **explicitly request a specific field by name** still receive null values if that field is null. This preserves the documented contract: "when you ask for a specific field, you get it, including the null if it's null."

**Implementation:**

Location: `src/mcp_atlassian/models/jira/issue.py:627–630`

```python
if self.requested_fields == "*all":
    for internal_id, field_data_obj in self.custom_fields.items():
        processed_value = self._process_custom_field_value(field_data_obj.get("value"))
        if processed_value is None or (isinstance(processed_value, list) and not processed_value):
            continue  # ← Skip null and empty-list fields
        # ... include in output
elif isinstance(self.requested_fields, list):
    # ... explicit field requests get their value regardless
```

**Tests:**

- `tests/unit/jira/test_issues.py::TestIssuesMixin::test_get_issue_all_fields_excludes_null_custom_fields` — Regression guard for TOR-01-twYUvG9

**Related TOR:** TOR-01-twYUvG9 (Jira Field Completeness)

---

## 2. DEFAULT_READ_JIRA_FIELDS Upstream Alignment

**Decision:** `DEFAULT_READ_JIRA_FIELDS` in `src/mcp_atlassian/jira/constants.py` contains exactly 10 fields, matching the upstream sooperset/mcp-atlassian project.

**Rationale:**

Upstream sets 10 core fields as the default for all read operations: `summary`, `description`, `status`, `assignee`, `reporter`, `labels`, `priority`, `created`, `updated`, `issuetype`. This is a deliberate minimal set to reduce API payload and LLM context overhead on most Jira instances.

An 18-field expansion (`XG1cmts` branch) existed at one point but was never merged into `peakflames/main`, so no code change was required to verify or correct this.

**Verification:**

`tests/unit/jira/test_constants.py::TestDefaultReadJiraFields` — 4 tests confirm the set is exactly these 10 fields, no extras, no removals.

**Related TOR:** TOR-01-sa52UmE (Jira Field Completeness)

---

## 3. Mixin Composition Pattern

**Decision:** Client classes (`JiraFetcher`, `ConfluenceFetcher`) inherit from multiple focused mixins rather than having monolithic base classes.

**Rationale:**

Atlassian products offer 50–70 distinct operations (search, CRUD, workflow, attachment, comment, metadata, etc.). A single 5000-line class becomes unmaintainable. Mixins:
- Group related operations (e.g., all sprint operations in `sprints.py`)
- Make it clear where a method belongs (grep `jira/sprints.py` to find sprint logic)
- Enable parallel development (two developers can work on separate mixins)
- Simplify testing (test mixin behavior in isolation, then composition)

**Structure:**

```python
class JiraFetcher(JiraIssuesMixin, JiraProjectsMixin, JiraFieldsMixin, ...):
    """Composed Jira client with 21 mixins."""
    def __init__(self, config: JiraConfig):
        self.config = config
        # Mixin __init__ calls happen implicitly in Python MRO
```

**Related TOR:** TOR-02 (Multi-Instance OAuth) — will extend mixin pattern to per-tenant client factories

---

## 4. Dependency Injection via MCP Context

**Decision:** FastMCP server injects `JiraFetcher` and `ConfluenceFetcher` instances into tool handlers via MCP context, not global singletons.

**Rationale:**

Global singletons prevent:
- Per-user authentication (OAuth with separate tokens per user)
- Per-workspace isolation (multi-tenant deployments)
- Clean test mocking (hard to swap out a global)

By injecting via context, each request can carry its own authenticated client.

**Implementation:**

Location: `servers/dependencies.py`

```python
def get_jira_fetcher(ctx: Context) -> JiraFetcher:
    """Resolve the user's Jira client from context (OAuth, Basic, PAT)."""
```

Tool handler example:

```python
@jira.tool()
def jira_get_issue(ctx: Context, issue_key: str) -> dict:
    fetcher = get_jira_fetcher(ctx)
    issue = fetcher.get_issue(issue_key)
    return issue.to_simplified_dict()
```

**Related TOR:** TOR-02 (Multi-Instance OAuth)

---

## 5. Pydantic v2 for All Models

**Decision:** All data models extend `ApiModel` (Pydantic v2 BaseModel) with standardized `from_api_response()` and `to_simplified_dict()` methods.

**Rationale:**

- **Validation:** Pydantic enforces type safety at the API boundary (raw dict → model)
- **Null handling:** Declarative field defaults prevent silent None propagation
- **Serialization:** `to_simplified_dict()` standardizes which fields go to the LLM (no accidental secrets or PII)
- **Consistency:** Every model follows the same pattern, reducing cognitive load for new contributors

**Example:**

```python
class JiraIssue(ApiModel):
    key: str
    summary: str
    description: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    
    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "JiraIssue":
        """Parse /rest/api/3/issue/KEY response."""
        return cls(**data)
    
    def to_simplified_dict(self) -> dict[str, Any]:
        """Return only fields safe for LLM."""
        # Null filter for *all requests (Epic Rm1iZNA) applied here
```

---

## Known Issues and Deferred Work

### Pre-existing Issues (not introduced by Epic Rm1iZNA)

- **Unreachable code warning:** `issue.py:260` — mypy reports unreachable statement (low priority, no behavior impact)
- **Windows path test failure:** `tests/test_attachments.py` — `/tmp/` vs `C:\tmp\` mismatch on Windows runners (isolated to attachment test, pre-existing)

### Deferred in Epic Rm1iZNA

- None identified.

### Epic-to-Epic Dependencies

- **TOR-02 (Multi-Instance OAuth)** — depends on foundation laid by Epic Rm1iZNA (null filtering, field defaults); will extend per-instance client factories

---

## Architectural Invariants

1. **Null filtering is read-only:**  `to_simplified_dict()` filters; request handling does not mutate API response payloads.

2. **Explicit requests bypass filtering:**  If a caller asks for a specific field, they get its value, even if null.

3. **Cloud and Server/DC branches are stable:**  Code uses `is_cloud` to partition logic; both paths are tested in CI.

4. **Tool naming is consistent:**  `{service}_{action}_{target}` pattern held across all 72 Jira + 45+ Confluence tools.

5. **Models are immutable in transit:**  Pydantic models are frozen at the API boundary; no in-flight mutation.

---

## Testing Strategy

**Unit tests** (fast, isolated):
- Model serialization (`to_simplified_dict()` output)
- Field filtering logic
- Field constant verification

**Integration tests** (require real Jira/Confluence):
- End-to-end OAuth flow
- Per-tenant client isolation
- API compatibility (Cloud vs Server/DC)

**Pre-commit hooks:**
- Ruff linting (88-char line length)
- mypy type checking (strict mode on src/, relaxed on tests/)
- Import sorting

---

## Future Considerations

1. **Performance optimization:** Cache field definitions per instance to reduce descriptor API calls.
2. **Extended field support:** Support for Automation, Templating, and custom structured data.
3. **Rate limit handling:** Backoff strategy for high-volume search and bulk operations.
4. **Workspace federation:** Support for Jira Service Management, Portfolio, and Automation Server.

---

*Last reviewed:** 2026-07-14 (Epic Rm1iZNA completion)  
*Next review:** After Epic 1SVldWi completion (TOR-02 Multi-Instance OAuth)
