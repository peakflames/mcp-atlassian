# Architecture

## System Overview

MCP Atlassian is a Model Context Protocol (MCP) server that bridges Atlassian products (Jira and Confluence) with AI language models. It provides 73 tools (49 Jira, 24 Confluence) for querying and managing Jira issues, Confluence pages, and related resources across both Cloud and Server/Data Center deployments.

The server exposes two independent FastMCP instances (`jira_mcp` and `confluence_mcp`), each wrapping domain-specific client fetchers (`JiraFetcher` and `ConfluenceFetcher`) that compose multiple mixins for feature organization.

---

## Technology Stack

| Layer | Technology | Version | Role |
|-------|-----------|---------|------|
| **Language** | Python | 3.10+ | Core runtime |
| **Framework** | FastMCP | 2.13–2.14 | MCP server scaffolding |
| **HTTP Client** | httpx, requests | 0.28+, 2.31+ | API transport + OAuth |
| **Data Validation** | Pydantic | 2.10+ | Type-safe models and schema |
| **Auth & Tokens** | keyring | 25.6+ | Secure token storage (OS keyring) |
| **Content Conversion** | markdownify, BeautifulSoup4 | 0.11+, 4.12+ | ADF/Storage → Markdown |
| **Web Framework** | Starlette, Uvicorn | 0.49+, 0.27+ | HTTP server for OAuth callbacks |
| **Caching** | cachetools, fakeredis | 5.0+, 2.32+ | In-memory and test caches |
| **Task Async** | Trio | 0.29+ | Async runtime option |
| **CLI** | Click | 8.1+ | Command-line interface |

---

## Repository Structure

```
.
├── src/mcp_atlassian/
│   ├── jira/                    # Jira client + 21 mixins
│   │   ├── client.py            # JiraFetcher base class
│   │   ├── config.py            # JiraConfig dataclass
│   │   ├── issues.py            # Issue CRUD mixin
│   │   ├── search.py            # JQL search mixin
│   │   ├── fields.py            # Field metadata mixin
│   │   ├── field_options.py     # Field picklist values
│   │   ├── boards.py            # Agile board queries
│   │   ├── sprints.py           # Sprint management
│   │   ├── sla.py               # SLA metrics
│   │   ├── metrics.py           # Advanced metrics
│   │   ├── comments.py          # Comment operations
│   │   ├── attachments.py       # File operations
│   │   ├── projects.py          # Project metadata
│   │   ├── links.py             # Issue link types
│   │   ├── epics.py             # Epic queries (Agile)
│   │   ├── transitions.py       # Workflow transitions
│   │   ├── users.py             # User queries
│   │   ├── watchers.py          # Watcher management
│   │   ├── worklog.py           # Time tracking
│   │   ├── development.py       # DevOps/deployment tracking
│   │   ├── queues.py            # Service Desk queues
│   │   ├── forms.py             # Forms UI (legacy; not composed into JiraFetcher)
│   │   ├── forms_api.py         # Forms REST API mixin (supersedes forms.py)
│   │   ├── forms_common.py      # Shared ProForma helpers
│   │   ├── formatting.py        # ADF formatting utilities
│   │   ├── constants.py         # Field constants
│   │   ├── utils.py             # Jira-specific helpers (JQL reserved words, etc.)
│   │   └── protocols.py         # Type protocols
│   │
│   ├── confluence/              # Confluence client + 8 mixins
│   │   ├── client.py            # ConfluenceFetcher base
│   │   ├── config.py            # ConfluenceConfig
│   │   ├── pages.py             # Page CRUD
│   │   ├── search.py            # CQL search
│   │   ├── comments.py          # Comment operations
│   │   ├── attachments.py       # File operations
│   │   ├── spaces.py            # Space queries
│   │   ├── labels.py            # Label management
│   │   ├── users.py             # User search
│   │   ├── analytics.py         # Page analytics
│   │   ├── v2_adapter.py        # Cloud v2 API adapter
│   │   ├── constants.py         # Constants
│   │   ├── utils.py             # Confluence-specific helpers (CQL reserved words, etc.)
│   │   └── protocols.py         # Type protocols
│   │
│   ├── models/                  # Pydantic v2 data models
│   │   ├── base.py              # ApiModel base class
│   │   ├── constants.py         # Shared model-layer constants
│   │   ├── jira/                # Jira-specific models
│   │   └── confluence/          # Confluence-specific models
│   │
│   ├── servers/                 # FastMCP server instances
│   │   ├── main.py              # Main server entry point
│   │   ├── jira.py              # Jira MCP tool definitions
│   │   ├── confluence.py        # Confluence MCP tool definitions
│   │   ├── context.py           # Request context
│   │   ├── dependencies.py      # Dependency injection
│   │   ├── client_storage.py    # OAuth token storage config
│   │   └── oauth_proxy.py       # Hardened OAuth proxy
│   │
│   ├── preprocessing/           # Content transformation
│   │   ├── base.py              # Base converter
│   │   ├── jira.py              # Jira ADF → Markdown
│   │   └── confluence.py        # Confluence Storage → Markdown
│   │
│   ├── utils/                   # Shared utilities
│   │   ├── oauth.py             # OAuth 2.0 config + flow
│   │   ├── access_control.py    # Per-project/space BLOCKED/READONLY enforcement
│   │   ├── ssl.py               # SSL verification
│   │   ├── urls.py              # URL validation + routing
│   │   ├── logging.py           # Structured logging + masking
│   │   ├── env.py               # Environment variable parsing
│   │   ├── tools.py             # Tool enable/disable logic
│   │   ├── toolsets.py          # Toolset filtering
│   │   ├── oauth_setup.py       # OAuth interactive setup
│   │   ├── token_verifier.py    # JWT verification
│   │   ├── decorators.py        # Common decorators
│   │   ├── io.py                # File/cache I/O
│   │   ├── lifecycle.py         # Startup/shutdown hooks
│   │   ├── date.py              # Date utilities
│   │   ├── media.py             # Media type detection
│   │   └── environment.py       # Service availability
│   │
│   └── exceptions.py            # Custom exceptions
│
├── tests/
│   ├── unit/                    # Unit tests
│   └── integration/             # Integration tests (requires real Atlassian)
│
├── scripts/                     # OAuth setup and utilities
├── Dockerfile                   # Multi-stage production image
├── pyproject.toml              # Dependencies, build config, scripts
├── CLAUDE.md                   # Project and fork guidelines (for agents)
└── AGENTS.md                   # Audience guide for LLM agents
```

---

## Architecture Patterns

### 1. **Mixin Composition**

Clients are built using mixin classes for feature organization:

```python
# JiraFetcher composes 21 mixins (src/mcp_atlassian/jira/__init__.py):
class JiraFetcher(
    ProjectsMixin,
    FieldsMixin,
    FieldOptionsMixin,
    FormsApiMixin,   # Forms REST API — supersedes the legacy FormsMixin (forms.py)
    FormattingMixin,
    TransitionsMixin,
    WorklogMixin,
    EpicsMixin,
    CommentsMixin,
    SearchMixin,
    IssuesMixin,
    UsersMixin,
    WatchersMixin,
    BoardsMixin,
    SprintsMixin,
    QueuesMixin,
    AttachmentsMixin,
    LinksMixin,
    MetricsMixin,
    SLAMixin,
    DevelopmentMixin,
):
    pass

# ConfluenceFetcher composes 8 mixins (src/mcp_atlassian/confluence/__init__.py):
class ConfluenceFetcher(
    SearchMixin,
    SpacesMixin,
    PagesMixin,
    CommentsMixin,
    LabelsMixin,
    UsersMixin,
    AnalyticsMixin,
    AttachmentsMixin,
):
    pass
```

**Rationale:**
- Keeps methods logically grouped by domain (issues, search, SLA, etc.)
- Makes it easy to find and test related functionality
- Supports selective tool enablement (disable search tools while keeping issue tools, etc.)

---

### 2. **FastMCP Server Setup**

Two independent MCP servers are instantiated in `servers/main.py`:

```python
jira_mcp = FastMCP("jira", dependencies=[...])
confluence_mcp = FastMCP("confluence", dependencies=[...])

# Main server composes both as sub-servers
main_server = FastMCP(
    "Atlassian",
    dependencies=[...],
    server=StarletteWithLifespan(...)
)
```

**Rationale:**
- Isolation: Jira and Confluence configs are separate; one can fail without affecting the other
- Flexibility: Clients can instantiate just the Jira server or just Confluence
- Scalability: Each server has its own dependency injection scope

---

### 3. **Dependency Injection**

The server lifespan initializes fetchers and injects them into request context:

```python
@asynccontextmanager
async def main_lifespan(app: FastMCP[MainAppContext]) -> AsyncIterator[dict[str, Any]]:
    services = get_available_services()
    loaded_jira_config: JiraConfig | None = None
    
    if services.get("jira"):
        jira_config = JiraConfig.from_env()
        if jira_config.is_auth_configured():
            loaded_jira_config = jira_config
    
    app.ctx.jira_config = loaded_jira_config
    # Similar for Confluence
    yield
```

Tools retrieve the fetcher via `get_jira_fetcher(request.ctx)`.

**Rationale:**
- Configuration is loaded once at startup
- Fetchers are reused across requests (pooled connections)
- Easy to mock in tests

---

### 4. **Authentication & OAuth**

Authentication is configured via environment variables and supports three modes:

| Mode | Config | Used By | Tokens Stored |
|------|--------|---------|---------------|
| **Basic (API Token)** | `JIRA_USERNAME` + `JIRA_API_TOKEN` | Cloud + Server/DC | No (stateless) |
| **PAT (Personal Access Token)** | `JIRA_PERSONAL_TOKEN` | Server/DC only | No (stateless) |
| **OAuth 2.0** | `JIRA_OAUTH_*` vars | Cloud + Server/DC | Yes (keyring) |

OAuth flow:
1. User runs `uv run mcp-atlassian --oauth-setup`
2. Browser redirects to Atlassian authorization endpoint
3. Authorization code is exchanged for access + refresh tokens
4. Tokens are verified + stored in OS keyring
5. On subsequent runs, tokens are loaded and refreshed as needed

**Multi-Instance Behavior (Epic 1SVldWi):**
- When `ATLASSIAN_OAUTH_CLOUD_ID` is configured, the system validates that the token's accessible resources include that site
- If validation fails, an actionable error is logged **before** tokens are cached, preventing bad tokens from being written to keyring
- Resolution priority: sole resource → its ID; multiple resources + configured ID → configured ID; else → first resource (backward-compatible fallback)

---

### 5. **Data Models**

All API responses are deserialized into Pydantic v2 models extending `ApiModel`:

```python
class ApiModel(BaseModel):
    """Base model for all API responses."""
    
    @classmethod
    def from_api_response(cls, data: Any) -> Self:
        """Deserialize API response data into model."""
        # Custom field mapping, null handling, etc.
    
    def to_simplified_dict(self) -> dict[str, Any]:
        """Convert to simplified dict for LLM consumption."""
        # Removes internal fields, shortens keys, masks sensitive data
```

Models are defined for:
- Jira: issues, projects, sprints, boards, comments, workflows, SLA, etc.
- Confluence: pages, spaces, comments, attachments, labels, analytics, etc.

**`*all` field requests (Epic jirafix):** `jira_get_issue(fields="*all")` passes the `*all` sentinel directly to the Jira REST API (rather than substituting the default 10-field set), so every populated field — including custom fields — is returned. `JiraIssue.to_simplified_dict()` then filters out custom fields whose value is `null` or an empty list, so instances with large custom-field catalogs don't flood LLM context with empty entries. Explicit field-name requests are unaffected — the filter only applies to the `*all` branch.

---

### 6. **Content Preprocessing**

Raw API content is converted to Markdown before being returned to the client:

| Source Format | Target | Converter |
|---|---|---|
| Jira ADF (Atlassian Document Format) | Markdown | `preprocessing/jira.py` |
| Confluence Storage (XML-like) | Markdown | `preprocessing/confluence.py` |

This normalizes content across Cloud and Server/Data Center, which use different internal formats.

---

### 7. **Tool Enablement & Filtering**

Tools can be selectively enabled/disabled via:
- `ENABLED_TOOLS` (comma-separated list, e.g., `jira_search,jira_create_issue`)
- `DISABLED_TOOLS` (comma-separated list, e.g., `jira_delete_issue`)
- `READ_ONLY_MODE=true` (blocks all write tools)
- Toolset filters (e.g., `ENABLED_JIRA_TOOLSETS=core,search`)

**Entry points:**
- `utils/tools.py` — individual tool enable/disable
- `utils/toolsets.py` — group filtering (core, search, agile, service-desk, admin)

---

## Tool Categories & Counts

### Jira Tools (49 total)

| Category | Count | Examples |
|----------|-------|----------|
| **Issues** | 7 | get, search, create, batch create, update, delete, batch get changelogs |
| **Fields & Options** | 2 | search fields, get field options |
| **Projects & Versions** | 6 | get all projects, get project issues/versions/components, create version, batch create versions |
| **Comments** | 2 | add, edit comment |
| **Attachments** | 2 | download attachments, get issue images |
| **Agile (Boards/Sprints)** | 7 | get boards, get sprints, get board/sprint issues, create/update sprint, add issues to sprint |
| **Links & Epics** | 5 | get link types, create/remove issue link, create remote issue link, link to epic |
| **Transitions** | 2 | get transitions, transition issue |
| **Watchers** | 3 | get, add, remove watchers |
| **Worklog** | 2 | add/get work logs |
| **Development** | 2 | get issue/issues development info |
| **SLA & Dates** | 2 | get issue SLA, get issue dates |
| **Service Desk / Queues** | 3 | get service desk, get queues, get queue issues |
| **Forms** | 3 | get proforma forms, get form details, update form answers |
| **User Profile** | 1 | get user profile |

### Confluence Tools (24 total)

| Category | Count | Examples |
|----------|-------|----------|
| **Pages** | 7 | get, get children, get space page tree, create, update, delete, move |
| **Search** | 2 | search pages, search users |
| **Comments** | 3 | get, add, reply to comments |
| **Labels** | 2 | get, add labels |
| **History / Diff / Views** | 3 | get page history, page diff, page views |
| **Attachments** | 6 | upload (single/multi), get, download (single/bulk), delete |
| **Images** | 1 | get page images |

---

## API Support Matrix

| Product | Deployment | Cloud API | Server/DC API | Auth Methods |
|---------|-----------|-----------|---------------|--------------|
| **Jira** | Cloud | REST v3 | N/A | Basic, OAuth |
| **Jira** | Server/DC | N/A | REST v2 + v3 | Basic, PAT, OAuth |
| **Confluence** | Cloud | v2 (+ legacy v1 fallback) | N/A | Basic, OAuth |
| **Confluence** | Server/DC | N/A | REST (legacy) | Basic, PAT, OAuth |

**Cloud vs Server/DC differences:**
- Jira Cloud uses REST v3 (stricter field filtering); Server/DC uses v2–v3 hybrids
- Confluence Cloud v2 API is newer; v1 fallback for backward compat
- API field names differ (e.g., `customFieldValue` vs custom field keys)
- OAuth token refresh is required for Cloud; optional for Server/DC (PAT alternative)

---

## Configuration & Environment

All configuration is loaded from environment variables at startup via `from_env()` factories:

```python
JiraConfig.from_env()  # Reads JIRA_URL, JIRA_USERNAME, JIRA_API_TOKEN, etc.
ConfluenceConfig.from_env()  # Reads CONFLUENCE_URL, etc.
```

See `.env.example` for all options. Key settings:

| Variable | Purpose |
|----------|---------|
| `JIRA_URL` / `CONFLUENCE_URL` | Instance base URL |
| `JIRA_USERNAME` / `CONFLUENCE_USERNAME` | User email (Cloud + Server/DC) |
| `JIRA_API_TOKEN` / `CONFLUENCE_API_TOKEN` | API token (Cloud + Server/DC) |
| `JIRA_PERSONAL_TOKEN` | PAT for Server/DC (alternative to username+token) |
| `JIRA_OAUTH_*` / `CONFLUENCE_OAUTH_*` | OAuth client credentials |
| `ATLASSIAN_OAUTH_CLOUD_ID` | Specific Atlassian Cloud site (for multi-tenant) |
| `READ_ONLY_MODE` | Set to `true` to block all write tools |
| `ENABLED_TOOLS` / `DISABLED_TOOLS` | Tool allowlist/blocklist |
| `HTTP_PROXY` / `HTTPS_PROXY` | Proxy settings |
| `SSL_VERIFY` | SSL cert verification (default: true) |

---

## Error Handling & Logging

Errors are logged with structured context:

```python
logger.error(f"Failed to get issue {issue_key}: {e}", exc_info=True)
```

Sensitive data (tokens, credentials) is masked in logs via `mask_sensitive()`.

Custom exceptions:
- `MCPAtlassianAuthenticationError` — auth/token failures
- `MCPAtlassianValidationError` — schema validation errors
- `MCPAtlassianAPIError` — API response errors

---

## Caching & Performance

- **API result caching**: In-memory TTL cache (via cachetools) for frequently accessed metadata (fields, projects)
- **Token refresh**: Automatic refresh with 5-minute margin (before expiry)
- **Connection pooling**: httpx + requests use persistent connections
- **Rate limiting**: Not enforced in client; relies on Atlassian's server-side limits

---

## Security

1. **Token Storage**: OAuth tokens stored in OS keyring (Windows Credential Manager, macOS Keychain, Linux Secret Service)
2. **SSL Verification**: Enabled by default; can be disabled via `SSL_VERIFY=false` (for testing only)
3. **CSRF Protection**: OAuth flow uses state parameter
4. **Credential Masking**: Tokens/passwords masked in logs
5. **SSRF Protection**: URL validation prevents redirect to internal hosts
6. **Read-only Mode**: `READ_ONLY_MODE=true` blocks all write operations at server level

---

## Testing

The test suite includes:

- **Unit tests** (`tests/unit/`): Model serialization, utility functions, mixin behavior — no external API calls
- **Integration tests** (`tests/integration/`): Real API calls to Cloud or Server/DC instances (requires credentials, marked with `@pytest.mark.integration`)
- **Quality gates** (`pre-commit`): Ruff (linting), mypy (type checking), formatting

Test command:
```bash
uv run pytest -xvs  # All tests
uv run pytest tests/unit/ -xvs  # Unit tests only
uv run pytest --cov=src/mcp_atlassian --cov-report=term-missing  # Coverage
```

---

## Deployment

The project publishes multi-platform Docker images via GitHub Actions:

**Dockerfile stages (2-stage build):**
1. **Builder (`uv` stage)** — `ghcr.io/astral-sh/uv:python3.13-alpine`; generates the lockfile and installs dependencies + the project via `uv sync --frozen --no-dev --no-editable`
2. **Final (runtime)** — `python:3.13-alpine`; copies only the built `.venv` from the builder stage, runs as non-root user `app`, entrypoint `mcp-atlassian`

Image tags follow semver + fork suffix:
- `sooperset/mcp-atlassian:0.21.2` (upstream)
- `peakflames/mcp-atlassian:0.21.2-peakflames.1` (peakflames fork, semver pre-release)

---

## Key Gotchas

1. **Cloud vs Server/DC**: Always check `is_cloud` before assuming API shape, field names, or auth behavior
2. **OAuth 2.0**: Multi-instance support requires `ATLASSIAN_OAUTH_CLOUD_ID` configuration; without it, first resource is used
3. **Token Validation**: OAuth tokens are validated against configured site **before** caching — bad tokens are never written to keyring
4. **Read-only Mode**: Set `READ_ONLY_MODE=true` at server startup; it blocks all write tools
5. **Sync Command Fix**: On Windows, use `uv sync --no-editable --frozen --all-extras --dev` (see CLAUDE.md)
