# Architecture — MCP Atlassian

> **Last refreshed:** 2026-07-14  
> **Basis:** Epic Rm1iZNA handoff + source inspection  
> **Scope:** Python ≥ 3.10, supports Jira and Confluence Cloud, Server, and Data Center

---

## Tech Stack

| Component | Technology | Version | Notes |
|-----------|-----------|---------|-------|
| **Runtime** | Python | ≥ 3.10 | Enforced by `pyproject.toml` |
| **Protocol** | Model Context Protocol (MCP) | ≥1.8.0, <2.0.0 | Anthropic specification |
| **Server** | FastMCP | ≥2.13.0, <2.15.0 | MCP server framework |
| **HTTP Framework** | Starlette | ≥0.49.1 | ASGI web framework |
| **ASGI Server** | Uvicorn | ≥0.27.1 | Production ASGI server |
| **Data Models** | Pydantic | ≥2.10.6, <3.0 | Validation and serialization |
| **HTTP Client** | Requests | ≥2.31.0 | Atlassian Python API dependency |
| **HTTP (async)** | HTTPX | ≥0.28.0 | Async HTTP for some operations |
| **HTML Parsing** | BeautifulSoup4 | ≥4.12.3 | Content preprocessing |
| **String Fuzzing** | TheFuzz | ≥0.22.1 | Field name matching |

---

## System Architecture

```
┌─────────────────────────────────────────────────────┐
│           FastMCP Server (Starlette/Uvicorn)        │
│  servers/main.py → lifespan → dependency injection  │
└─────────────────────────────────────────────────────┘
                        ↓
         ┌──────────────────────────────┐
         │  OAuth2 Proxy & Client Storage│
         │  (oauth_proxy.py, client_...) │
         └──────────────────────────────┘
                        ↓
         ┌──────────────────────────────┐
         │  Jira & Confluence Fetchers   │
         │ (Mixin composition pattern)   │
         └──────────────────────────────┘
                     ↙         ↖
         ┌──────────────┐   ┌──────────────┐
         │ Jira Client  │   │ Confluence   │
         │ + 21 Mixins  │   │ Client +8Mix │
         └──────────────┘   └──────────────┘
             ↓                      ↓
    ┌────────────────┐    ┌────────────────┐
    │ Jira API       │    │ Confluence API │
    │ (Cloud/DC)     │    │ (Cloud/DC)     │
    └────────────────┘    └────────────────┘
```

---

## Component Map

### 1. Server Layer (`servers/`)

| File | Purpose | Key Exports |
|------|---------|------------|
| `main.py` | FastMCP server entry point, lifespan, exception handling | `app: FastMCP` |
| `jira.py` | Jira tool definitions (72 tools) | `register_jira_tools()` |
| `confluence.py` | Confluence tool definitions (45+ tools) | `register_confluence_tools()` |
| `dependencies.py` | Dependency injection (`get_jira_fetcher`, `get_confluence_fetcher`) | Factory functions |
| `context.py` | MCP context management, user/tenant resolution | MCP context extensions |
| `client_storage.py` | Client storage for per-user OAuth state | Storage interface |
| `oauth_proxy.py` | OAuth 2.0 flow proxy (Cloud + Server/DC) | OAuth handlers |

### 2. Jira Client Layer (`jira/`)

**Base client:** `jira/client.py` (inherits from 21 mixins)

**Mixins** (mixed into `JiraFetcher`):
1. `issues.py` — Search, fetch, create, update issues
2. `projects.py` — Project enumeration, metadata
3. `fields.py` — Field definitions and custom field discovery
4. `field_options.py` — Enum/picker field options
5. `search.py` — JQL search with pagination
6. `comments.py` — Issue comments, edit, delete
7. `transitions.py` — Workflow transitions, status changes
8. `watchers.py` — Issue watchers and subscriber management
9. `attachments.py` — Attachment upload, download
10. `worklog.py` — Work tracking (time spent)
11. `links.py` — Issue links and blocking relationships
12. `epics.py` — Epic hierarchy (Agile)
13. `boards.py` — Agile board enumeration
14. `sprints.py` — Sprint management
15. `metrics.py` — SLA, cycle time, custom metrics
16. `sla.py` — SLA status and escalation data
17. `queues.py` — Service Desk queues
18. `users.py` — User profile and mentions
19. `development.py` — Development info (linked PRs, deployments)
20. `forms.py` — Proforma forms (IssueActions)
21. `forms_api.py` — Additional form endpoints

**Config:** `config.py` — `JiraConfig` dataclass (auth, URL, proxy settings)

**Constants:** `constants.py` — Reserved JQL words, `DEFAULT_READ_JIRA_FIELDS` (10 fields)

### 3. Confluence Client Layer (`confluence/`)

**Base client:** `confluence/client.py` (inherits from 8 mixins)

**Mixins** (mixed into `ConfluenceFetcher`):
1. `pages.py` — Page CRUD, hierarchy, content
2. `comments.py` — Page comments
3. `attachments.py` — Page attachments
4. `spaces.py` — Space enumeration and metadata
5. `search.py` — CQL search, full-text
6. `labels.py` — Label management
7. `analytics.py` — Page views, analytics
8. `users.py` — User search and profiles

**Config:** `config.py` — `ConfluenceConfig` dataclass

**Preprocessing:** `preprocessing/confluence.py` — ADF/Storage format → Markdown

### 4. Data Models (`models/`)

**Base:** `models/base.py`
- `ApiModel` — Base class with `from_api_response()` and `to_simplified_dict()`
- `TimestampMixin` — Datetime parsing

**Jira models** (`models/jira/`):
- `issue.py` — `JiraIssue` with null-filtering in `to_simplified_dict()` for `*all` requests
- `common.py` — `JiraUser`, `JiraStatus`, `JiraPriority`, `JiraIssueType`, `JiraProject`, `JiraAttachment`, `JiraChangelog`, `JiraTimetracking`
- `comment.py` — `JiraComment`
- `link.py` — `JiraIssueLink`
- `project.py` — `JiraProject`
- `search.py` — Search result models
- `agile.py` — `JiraBoard`, `JiraSprint`
- `sla.py`, `metrics.py`, `workflow.py`, `forms.py`, `queue.py`, `version.py`, `field_option.py` — Domain models
- `adf.py` — Atlassian Document Format parsing

**Confluence models** (`models/confluence/`):
- `page.py` — `ConfluencePage` with Storage/Body expanders
- `comment.py` — `ConfluenceComment`
- `space.py` — `ConfluenceSpace`
- `common.py` — `ConfluenceUser`, `ConfluenceAttachment`
- `label.py` — `ConfluenceLabel`
- `search.py` — Search result models
- `analytics.py` — View/analytics models

### 5. Utilities (`utils/`)

| File | Purpose |
|------|---------|
| `auth.py` | Basic, PAT, OAuth 2.0 auth factories |
| `oauth.py` | OAuth 2.0 token refresh, session management |
| `oauth_setup.py` | CLI wizard for OAuth setup |
| `env.py`, `environment.py` | Environment variable parsing |
| `decorators.py` | Rate-limit, retry, cache decorators |
| `ssl.py` | Custom SSL/TLS certificate handling |
| `logging.py` | Structured logging configuration |
| `io.py` | File I/O, attachment handling |
| `media.py` | Media type detection |
| `lifecycle.py` | Server startup/shutdown hooks |
| `token_verifier.py` | Token validation and expiry checks |
| `access_control.py` | Per-project and per-space permission controls |
| `tools.py` | Tool metadata registration |
| `toolsets.py` | Tool grouping for discovery |

### 6. Preprocessing (`preprocessing/`)

| File | Purpose |
|------|---------|
| `base.py` | Base converter interface |
| `jira.py` | ADF and JIRA markup → Markdown |
| `confluence.py` | Confluence Storage/Body ADF → Markdown |

---

## Tool Naming Convention

All tools follow the pattern:

```
{service}_{action}_{target}
```

**Examples:**
- `jira_search_issues` — Jira service, search action, issues target
- `jira_create_issue` — Jira service, create action, issue target
- `confluence_get_page` — Confluence service, get action, page target
- `confluence_add_comment` — Confluence service, add action, comment target

**Jira Tools (72 total):**
- Search: `jira_search`, `jira_search_fields`, `jira_get_issues_development_info`
- Issues: `jira_get_issue`, `jira_create_issue`, `jira_update_issue`, `jira_batch_create_issues`, `jira_get_issue_dates`, `jira_get_issue_development_info`, `jira_get_issue_proforma_forms`
- Transitions: `jira_get_transitions`, `jira_transition_issue`
- Comments: `jira_add_comment`, `jira_edit_comment`
- Attachments: `jira_download_attachments`
- Links: `jira_create_issue_link`, `jira_create_remote_issue_link`, `jira_get_link_types`
- Watchers: `jira_add_watcher`, `jira_get_issue_watchers`
- Worklog: `jira_add_worklog`, `jira_get_worklog`
- Sprints: `jira_get_sprints_from_board`, `jira_get_sprint_issues`, `jira_add_issues_to_sprint`, `jira_create_sprint`, `jira_update_sprint`
- Boards: `jira_get_agile_boards`, `jira_get_board_issues`
- Epics: `jira_link_to_epic`
- Projects: `jira_get_all_projects`, `jira_get_project_issues`, `jira_get_project_components`, `jira_get_project_versions`
- Versions: `jira_create_version`, `jira_batch_create_versions`, `jira_get_project_versions`
- Fields: `jira_get_field_options`, `jira_search_fields`
- SLA: `jira_get_issue_sla`
- Queue: `jira_get_queue_issues`, `jira_get_service_desk_queues`, `jira_get_service_desk_for_project`
- Forms: `jira_get_proforma_form_details`, `jira_update_proforma_form_answers`
- Images: `jira_get_issue_images`
- Users: `jira_get_user_profile`

**Confluence Tools (45+ total):**
- Pages: `confluence_get_page`, `confluence_create_page`, `confluence_update_page`, `confluence_move_page`, `confluence_get_page_children`, `confluence_get_page_history`, `confluence_get_page_diff`, `confluence_get_page_views`
- Search: `confluence_search`, `confluence_search_user`
- Comments: `confluence_get_comments`, `confluence_add_comment`, `confluence_reply_to_comment`
- Attachments: `confluence_get_attachments`, `confluence_upload_attachment`, `confluence_upload_attachments`, `confluence_download_attachment`, `confluence_download_content_attachments`, `confluence_get_page_images`
- Labels: `confluence_get_labels`, `confluence_add_label`

---

## Authentication

**Supported methods:**

| Method | Jira Cloud | Jira Server/DC | Confluence Cloud | Confluence Server/DC |
|--------|-----------|---|-----------|---|
| **Basic Auth** (user + API token) | ✓ | ✓ | ✓ | ✓ |
| **Personal Access Token (PAT)** | — | ✓ | — | ✓ |
| **OAuth 2.0** | ✓ | ✓ | ✓ | ✓ |

**OAuth 2.0 flow:**
1. User runs `mcp-atlassian --oauth-setup`
2. CLI opens browser for authorization grant
3. Server receives callback, exchanges code for token
4. Token persisted in `client_storage` (Redis or file-based)
5. Token refreshed automatically before expiry

---

## Data Model Pattern

All response models extend `ApiModel` and implement:

```python
@classmethod
def from_api_response(cls, data: dict[str, Any]) -> "ModelName":
    """Parse raw API response, handle null/missing fields safely."""
    
def to_simplified_dict(self) -> dict[str, Any]:
    """Return filtered dict for LLM consumption."""
```

**Key rule (Epic Rm1iZNA):** When `requested_fields == "*all"` in Jira issue retrieval, `to_simplified_dict()` excludes custom fields with null or empty-list values to prevent context flooding in instances with 2000+ custom fields.

---

## Field Filtering

**Jira read operations:**
- `DEFAULT_READ_JIRA_FIELDS` = 10 upstream fields: `summary`, `description`, `status`, `assignee`, `reporter`, `labels`, `priority`, `created`, `updated`, `issuetype`
- If caller requests `fields='*all'`, all non-null, non-empty custom fields are returned
- If caller requests explicit list, only those fields returned (including nulls if explicitly requested)
- Custom fields with null/empty-list values **excluded** from `*all` responses (Epic Rm1iZNA)

---

## Cloud vs Server/Data Center

**Key differences handled by code:**

| Feature | Cloud | Server/DC |
|---------|-------|-----------|
| **User types** | Jira Cloud users | System users + LDAP |
| **API auth** | API token only | API token + PAT + OAuth |
| **Field IDs** | Consistent (`customfield_XXXXX`) | May vary per instance |
| **Attachment URLs** | Direct download | May require auth header |
| **Agile endpoints** | `/rest/agile/1.0/` | `/rest/agile/1.0/` |
| **Status check** | `GET /rest/api/2/status` | Instance-specific statuses |

**Code pattern:**
```python
if self.is_cloud:
    # Cloud-specific logic
else:
    # Server/DC-specific logic
```

---

## Known Architectural Decisions

1. **Mixin composition over inheritance chains** — Keeps mixins focused, avoids deep hierarchies
2. **Model base class (`ApiModel`)** — Centralized null-handling and serialization
3. **Dependency injection via context** — Server lifespan injects clients, enables per-user auth
4. **Content preprocessing** — ADF/Storage formats converted to Markdown before LLM delivery
5. **Tool registration pattern** — Tools registered in `servers/{jira,confluence}.py`, not scattered
6. **Field filtering for context efficiency** — Default 10 fields + selective null exclusion in `*all` mode

---

## Deployment

**Entry point:** `mcp_atlassian:main` (click CLI)

**Output:** FastMCP server listening on stdio or HTTP transport

**Docker:** Multi-stage build, Python ≥ 3.10 runtime, Uvicorn in container

---

*This document is generated from source inspection and epic handoffs. For the latest design decisions and deferred work, see `docs/design-notes.md`.*
