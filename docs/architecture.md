# Architecture

## Overview

**MCP Atlassian** is a Model Context Protocol (MCP) server for Atlassian products (Jira and Confluence). It enables secure, contextual AI interactions with Atlassian tools while maintaining data privacy. The server supports both Cloud and Server/Data Center deployments.

## Tech Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| Language | Python | ≥ 3.10 |
| Web Framework | FastMCP + Starlette | 2.13.0–2.14.x |
| Data Serialization | Pydantic | v2.10.6+ |
| HTTP Client | httpx, requests | 0.28.0+, 2.31.0+ |
| Process Runtime | Trio | 0.29.0+ |
| Cache | cachetools (TTLCache) | 5.0.0+ |
| Session Store | FakeRedis | 2.32.1–2.34.x |

## System Architecture

### High-Level Components

```
┌─────────────────────────────────────────────────────┐
│       Claude AI / LLM Clients (via MCP)             │
└──────────────┬──────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────┐
│  FastMCP Servers (Lifespan Container)               │
│  ├─ jira_mcp (Jira tools)                           │
│  └─ confluence_mcp (Confluence tools)               │
└──────────────┬──────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────┐
│  Dependency Injection (Context)                     │
│  ├─ get_jira_fetcher(ctx)                          │
│  └─ get_confluence_fetcher(ctx)                    │
└──────────────┬──────────────────────────────────────┘
               │
       ┌───────┴────────┬─────────────┐
       ▼                ▼             ▼
   ┌────────┐    ┌──────────┐    ┌─────────┐
   │  Jira  │    │Confluence│    │  OAuth  │
   │Fetcher │    │ Fetcher  │    │  Proxy  │
   │(21 mx) │    │(8 mixins)│    │ Handler │
   └────┬───┘    └────┬─────┘    └────┬────┘
        │             │               │
        │    ┌────────┼───────────┐   │
        ▼    ▼        ▼           ▼   ▼
   ┌────────────────────────────────────┐
   │  Atlassian Cloud/DC REST APIs      │
   │  (Jira + Confluence)               │
   └────────────────────────────────────┘
```

### Mixin Composition

**JiraFetcher** composes 21 mixins (via transitive inheritance through JiraClient):

1. **IssuesMixin** — issue queries, lifecycle, watchers
2. **SearchMixin** — JQL search, field expansion
3. **AttachmentsMixin** — download/upload attachments
4. **EpicsMixin** — epic link management
5. **FieldsMixin** — field definitions, custom field options
6. **BoardsMixin** — agile board queries, sprints
7. **CommentsMixin** — issue and user comments
8. **WorklogMixin** — time tracking entries
9. **MetricsMixin** — issue metrics (cycle time, velocity)
10. **SLAMixin** — SLA metrics and goals
11. **DevelopmentMixin** — development information (branches, PRs, builds)
12. **ProjectsMixin** — project details, versions, components
13. **LinksMixin** — issue linking and link types
14. **TransitionsMixin** — workflow transitions
15. **UsersMixin** — user profiles and search
16. **FormsMixin** — Jira Forms (proforma) API
17. **QueuesMixin** — service desk queues
18. **CachingMixin** — TTLCache wrapping API responses
19. Additional internal mixins for protocols and utilities

**ConfluenceFetcher** composes 8 mixins:

1. **PagesMixin** — CRUD operations on pages, hierarchy, history
2. **SearchMixin** — CQL search, space browsing
3. **CommentsMixin** — page and content comments
4. **AttachmentsMixin** — attachment CRUD
5. **AnalyticsMixin** — page views and engagement metrics
6. **LabelsMixin** — page labels and tagging
7. **SpacesMixin** — space navigation and metadata
8. **UsersMixin** — user profiles and search

### FastMCP Server & Lifespan

- **Entry point**: `servers/main.py` (module-level FastMCP server instance)
- **Lifespan hooks**: Async context manager runs during server startup/shutdown
- **OAuth setup**: On startup, checks for OAuth env vars and optionally sets up an OAuth proxy handler
- **Client instantiation**: `JiraFetcher` and `ConfluenceFetcher` are instantiated during lifespan and stored in the context
- **Dependency injection**: Tools access fetchers via `get_jira_fetcher(ctx)` and `get_confluence_fetcher(ctx)` calls

### Tool Naming Convention

Tools follow the pattern: `{service}_{action}_{target}`

**Examples:**
- `jira_get_issue` — Jira service, get action, issue target
- `jira_create_issue` — Jira service, create action, issue target
- `confluence_search` — Confluence service, search action (all)
- `confluence_get_page` — Confluence service, get action, page target
- `confluence_add_comment` — Confluence service, add action, comment target

### Authentication

Three authentication methods are supported:

| Method | Cloud | Server/DC | Implementation |
|--------|-------|----------|-----------------|
| Basic (API Token) | ✅ | ✅ | `username` + `api_token` env vars |
| Basic (User Token) | ❌ | ✅ | `username` + `user_token` env vars |
| Personal Access Token (PAT) | ❌ | ✅ | `personal_token` env var |
| OAuth 2.0 | ✅ | ✅ | `JiraOAuthConfig` + proxy handler |

**Multi-tenant header support**: The `X-Atlassian-Tenant-ID` header is automatically added for Cloud deployments when a tenant ID is configured.

**Read-only mode**: Setting `READ_ONLY_MODE=true` blocks all write tools at the FastMCP server level (before tool execution).

### Configuration

Configuration is loaded via two dataclasses:

- **JiraConfig** — Jira connection params, auth method, filtering, SLA rules
- **ConfluenceConfig** — Confluence connection params, auth method, filtering

Both inherit from `AtlassianBaseConfig` which provides:
- `from_env()` factory method — loads all env vars matching a prefix
- Field validation and SSL certificate handling

### Data Models

All models extend an `ApiModel` base class providing:
- **`from_api_response(data: dict)`** — factory constructor from raw API response
- **`to_simplified_dict()`** — conversion to simplified dict for LLM consumption
- Field documentation and type hints (Pydantic v2)

Model categories:

- **Jira models** (`models/jira/`): `JiraIssue`, `JiraComment`, `JiraWorklog`, `JiraSprint`, `JiraProject`, etc.
- **Confluence models** (`models/confluence/`): `ConfluencePage`, `ConfluenceComment`, `ConfluenceSpace`, `ConfluenceAttachment`, etc.
- **Common models** (`models/jira/common.py`, etc.): `JiraChangelog`, `JiraUser`, pagination metadata

### Content Preprocessing

Two conversion pipelines handle non-plaintext content:

- **ADF to Markdown** (`preprocessing/jira.py`): Converts Atlassian Document Format to Markdown
- **Confluence Storage to Markdown** (`preprocessing/confluence.py`): Converts Confluence Storage XML to Markdown

These ensure Jira descriptions, Confluence pages, and comments are rendered as readable Markdown for LLMs.

### Access Control

The `utils/access_control.py` module provides project-level access controls:

- **Whitelist mode** (`JIRA_PROJECTS_FILTER`): Only specific projects are allowed
- **Blocklist mode** (`JIRA_PROJECT_BLOCKED` / `JIRA_PROJECT_READONLY`): Per-project BLOCKED or READONLY access levels

Access checks run **before** API calls and raise `ProjectAccessError` if access is denied.

### Caching Strategy

- **CachingMixin**: Wraps API responses in a TTLCache with configurable TTL (default: 5 minutes)
- **Session store**: FakeRedis backend stores OAuth tokens and client session data
- **Cache key**: Derived from method name + arguments

### Tools Summary

**Jira: 72 tools**
- Issue operations (get, create, update, transition, link, etc.)
- Search (JQL with field expansion)
- Agile (sprints, boards)
- Workflow (transitions, permissions)
- Metrics (SLA, cycle time, velocity)
- Development (branches, PRs, builds)
- Forms (proforma questionnaires)
- Service Desk (queues)

**Confluence: 43 tools**
- Page operations (get, create, update, move, history)
- Search (CQL)
- Comments and discussions
- Attachments
- Analytics (page views)
- Labels and tagging
- Spaces and navigation

## Important Reminders

### Cloud vs Server/DC

API endpoints, field names, and authentication methods differ between Jira Cloud and Server/Data Center. Always check `is_cloud` flag before assuming behavior.

### Field Expansion

The `DEFAULT_READ_JIRA_FIELDS` constant (18 fields) is used by default in `jira_get_issue`, `jira_search`, and `jira_get_board_issues`. When `fields='*all'` is passed, the API receives the `*all` sentinel (not an expanded list of defaults) to retrieve every populated field including custom fields.

### Type Checking

The pre-commit hook runs mypy in **strict mode**. All functions require type hints.

### Environment Configuration

See `.env.example` for all configuration options (auth methods, proxy, SLA rules, filtering).

## File Organization

```
src/mcp_atlassian/
├── jira/              # Jira client + 21 mixins
├── confluence/        # Confluence client + 8 mixins
├── models/            # Pydantic v2 data models
├── servers/           # FastMCP server instances
├── preprocessing/     # Content conversion (ADF/Storage → Markdown)
├── utils/             # Shared utilities (auth, logging, SSL, decorators)
└── exceptions.py      # Custom exceptions

tests/
├── unit/              # Unit tests (no external calls)
├── integration/       # Real API validation
└── fixtures/          # Test data and mocks

scripts/               # OAuth setup and testing utilities

docs/
├── architecture.md    # This file
├── design-notes.md    # Design decisions and rationale
├── implementation-plan/
│   ├── README.md
│   ├── status/        # Per-epic status sidecars
│   ├── session-handoffs/  # Epic completion handoffs
│   └── phase-*/       # Phase registries
├── requirements/      # TOR feature files
└── product-vision-planning/
    ├── product-vision.md
    └── concept-of-operations.md
```
