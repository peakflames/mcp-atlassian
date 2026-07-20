# Design Notes

Key architectural and design decisions made during implementation.

---

## 1. Mixin-Based Client Composition

**Decision:** Organize fetcher functionality into domain-specific mixins (JiraIssuesMixin, JiraSearchMixin, etc.) rather than monolithic client classes.

**Rationale:**
- **Maintainability**: Related methods stay together (all issue operations in one mixin, all search operations in another)
- **Reusability**: Mixins can be composed into different client types (e.g., read-only client without write mixins)
- **Testability**: Each mixin can be unit tested independently
- **Feature isolation**: Selective tool enablement via `utils/toolsets.py` is much simpler when methods are already grouped

**Trade-offs:**
- Method lookup requires understanding mixin ordering (solved via IDE search + comprehensive docs)
- Multiple inheritance adds cognitive load (mitigated by consistent naming and docstrings)

**Evidence:**
- 21 Jira mixins in `src/mcp_atlassian/jira/` + 8 Confluence mixins in `src/mcp_atlassian/confluence/`
- Tool filtering in `utils/toolsets.py` groups by mixin (e.g., `JiraSearchMixin` tools in `search` toolset)

---

## 2. Separate FastMCP Servers for Jira and Confluence

**Decision:** Instantiate two independent MCP servers (`jira_mcp`, `confluence_mcp`) within a parent `main_server`.

**Rationale:**
- **Isolation**: Jira and Confluence have separate configs, auth, and error handling. If one fails, the other continues
- **Flexibility**: Clients can mount just the Jira server or just Confluence (important for single-product teams)
- **Scalability**: Each server has its own dependency injection scope; easy to scale one separately
- **Clarity**: Tool discovery is simpler when tools are scoped to a specific product

**Evidence:**
- `servers/main.py` creates both servers within the lifespan
- `servers/jira.py` and `servers/confluence.py` define product-specific tools
- Configuration loading is independent: `JiraConfig.from_env()` and `ConfluenceConfig.from_env()` can fail separately

---

## 3. OAuth Cloud-Site Validation and Deterministic Resolution (Epic 1SVldWi)

**Decision:** When multiple Atlassian Cloud sites are accessible via the same OAuth token:
1. **Validate** that the configured `ATLASSIAN_OAUTH_CLOUD_ID` (if set) is in the token's accessible resources
2. **Reject immediately** if validation fails (raise `MCPAtlassianAuthenticationError` before caching tokens)
3. **Resolve deterministically**: sole resource → use its ID; multiple resources + configured ID → configured ID; else → first resource (backward compat)

**Rationale:**
- **Safety**: Validation prevents misconfiguration where the token grants access to Site A, but the config expects Site B. Without validation, users silently connect to the wrong site and get confusing errors
- **Fail-fast**: Rejection happens during OAuth setup (before tokens are cached), not during a tool call. Users see the error immediately with actionable guidance
- **Backward compat**: Single-site accounts are unaffected (sole resource check is first); existing deployments with no `ATLASSIAN_OAUTH_CLOUD_ID` continue to use the first site
- **Multi-tenant support**: Teams with accounts on multiple Atlassian Cloud instances can now configure which site to connect to

**Key Decisions:**
- Validation runs **before** resolution: capture configured ID, fetch resources, check membership, then resolve
- `MCPAtlassianAuthenticationError` is raised from `_get_cloud_id()` and caught by a dedicated `except` handler in `exchange_code_for_tokens()` — the handler logs the error at ERROR level and returns `False` **before** calling `_save_tokens()`
- Resolution priority order is: `len(resources) == 1` first (sole resource wins), then `configured_id` (explicit wins), then `resources[0]` (fallback)

**Error Message Example:**
```
OAuth authorization failed: the authorized account can access "Site A" (abc123), but the configured ATLASSIAN_OAUTH_CLOUD_ID requires site "xyz789". Re-run setup and sign in with an account that has access to the required site.
```

**Test Coverage:**
- `TOR-02-s6Jze5H` — configured ID resolved regardless of list position
- `TOR-02-IUNtYgO` — fallback to first resource when unconfigured
- `TOR-02-ePsqZQq` — sole resource used regardless of configured ID (with caveats; see Known Issues)
- `TOR-02-CE3OroW` — mismatch raises with both actual site name and required ID
- `TOR-02-MLk6Fcn` — matching configured ID accepted (no error)
- `TOR-02-6kYAHsQ` — `_save_tokens()` never reached when validation fails

**Evidence:**
- `src/mcp_atlassian/utils/oauth.py:310–368` — `_get_cloud_id()` implementation
- `src/mcp_atlassian/utils/oauth.py:242–244` — exception handler in `exchange_code_for_tokens()`
- `tests/unit/utils/test_oauth.py:TestMultiInstanceOAuthCloudIdResolution` — 6 regression tests

---

## 4. Pydantic v2 Models with Simplified Output

**Decision:** All API responses are deserialized into Pydantic v2 `BaseModel` subclasses extending `ApiModel`, with a `to_simplified_dict()` method for LLM consumption.

**Rationale:**
- **Type safety**: Validation at deserialization time catches API shape changes early
- **Schema consistency**: OpenAPI schema generation is automatic and consistent across all models
- **Content control**: `to_simplified_dict()` allows us to:
  - Remove internal fields (e.g., Jira's `_links`, Confluence's `_links`)
  - Shorten verbose keys (e.g., `issueFields.customfield_10000` → `custom_field_10000`)
  - Mask sensitive data
  - Normalize across Cloud vs Server/DC API variations
- **LLM-friendly**: Simplified output reduces token usage and confusion

**Trade-offs:**
- Extra deserialization overhead (minimal in practice; APIs are the bottleneck)
- Custom field mapping can be fragile if Atlassian changes field ID schemes

**Evidence:**
- `src/mcp_atlassian/models/base.py` — `ApiModel` base class
- All models in `src/mcp_atlassian/models/jira/` and `src/mcp_atlassian/models/confluence/` extend `ApiModel`

---

## 5. Environment-Based Configuration via `from_env()` Factories

**Decision:** Load all configuration from environment variables at startup via `from_env()` class methods on `JiraConfig` and `ConfluenceConfig`.

**Rationale:**
- **12-factor compliance**: Config separate from code
- **Multi-environment support**: Same Docker image runs in dev, staging, production with different env vars
- **No secrets in code**: Credentials are never hardcoded or in config files
- **IDE/MCP integration**: Claude Desktop and Cursor load env vars from `mcpServers` config in `settings.json`

**Configuration methods (in priority order):**
1. Environment variables (e.g., `JIRA_USERNAME`)
2. OS keyring (for OAuth tokens)
3. Defaults in code (fallback)

**Evidence:**
- `src/mcp_atlassian/jira/config.py` — `JiraConfig.from_env()`
- `src/mcp_atlassian/confluence/config.py` — `ConfluenceConfig.from_env()`
- `.env.example` — comprehensive list of all options

---

## 6. Content Preprocessing: ADF/Storage → Markdown

**Decision:** Convert raw Atlassian content formats (ADF for Jira, Storage for Confluence) to Markdown before returning to LLM.

**Rationale:**
- **Consistency**: Cloud and Server/DC use different internal formats (ADF vs Storage); Markdown normalizes them
- **LLM-friendly**: Markdown is more compact than ADF/Storage XML and natural for language models
- **Readability**: Users see Markdown in tool responses, not raw API formats
- **Formatting preservation**: Bold, italic, lists, code blocks are preserved

**Converters:**
- `preprocessing/jira.py` — ADF → Markdown (handles rich text, mentions, links, embeds)
- `preprocessing/confluence.py` — Storage → Markdown

**Trade-offs:**
- Complex ADF/Storage structures may not map perfectly to Markdown (e.g., multi-column layouts → text approximation)
- Converters must be maintained if Atlassian introduces new format features

**Evidence:**
- `src/mcp_atlassian/preprocessing/jira.py` — ADF parser
- `src/mcp_atlassian/preprocessing/confluence.py` — Storage parser
- Returns use `.to_simplified_dict()` which includes preprocessed `description`, `content`, etc. as Markdown

---

## 7. Tool Enablement Filtering via Environment

**Decision:** Allow operators to enable/disable tools at server startup via `ENABLED_TOOLS`, `DISABLED_TOOLS`, `READ_ONLY_MODE`, and `ENABLED_*_TOOLSETS` environment variables.

**Rationale:**
- **Security**: `READ_ONLY_MODE=true` blocks all write operations (no deletion, creation, updates)
- **Least privilege**: Teams can disable risky tools (e.g., disable `jira_delete_issue` if deletion is not needed)
- **Feature gating**: Disable search tools if CQL queries are expensive in your environment
- **Compliance**: Audit trails remain clean if certain operations are disabled from the start

**Filtering logic:**
- `ENABLED_TOOLS` (whitelist): If set, only these tools are available
- `DISABLED_TOOLS` (blacklist): If set, these tools are hidden
- `READ_ONLY_MODE=true` (kill switch): All write tools are disabled
- `ENABLED_JIRA_TOOLSETS` (group filter): e.g., `core,search` enables only tools in those toolsets

**Entry points:**
- `utils/tools.py` — per-tool filtering
- `utils/toolsets.py` — toolset grouping and filtering
- `servers/main.py` — applies filtering during tool registration

**Evidence:**
- `src/mcp_atlassian/utils/tools.py:get_enabled_tools()`, `should_include_tool()`
- `src/mcp_atlassian/utils/toolsets.py:get_enabled_toolsets()`, `should_include_tool_by_toolset()`
- Filtering applied in `servers/main.py` lifespan before tools are registered

---

## 8. FastMCP + Starlette HTTP Server for OAuth Callbacks

**Decision:** Use FastMCP's `StarletteWithLifespan` to run an HTTP server on localhost for OAuth callback handling.

**Rationale:**
- **Interactive setup**: `uv run mcp-atlassian --oauth-setup` launches a browser, user authorizes, browser redirects to localhost callback
- **Token exchange**: Callback endpoint exchanges authorization code for tokens
- **Transparent**: User runs one command; browser automation handles the rest
- **Secure**: Uses PKCE (Proof Key for Code Exchange) for extra security in public clients

**Flow:**
1. `--oauth-setup` flag triggers `oauth_setup.py`
2. Browser opens to Atlassian auth endpoint with `state` + `code_challenge`
3. User authorizes; Atlassian redirects to `http://localhost:PORT/callback?code=...&state=...`
4. Callback handler exchanges code for tokens via `OAuthConfig.exchange_code_for_tokens()`
5. Tokens are stored in keyring; user sees success/error message in browser

**Evidence:**
- `src/mcp_atlassian/utils/oauth_setup.py` — interactive setup
- `src/mcp_atlassian/servers/oauth_proxy.py` — callback handler
- `src/mcp_atlassian/servers/main.py:main_lifespan()` — server startup

---

## 9. Cloud vs Server/Data Center Abstraction

**Decision:** Detect the deployment type (Cloud vs Server/DC) based on URL shape and API responses, then conditionally apply Cloud-specific or DC-specific logic.

**Rationale:**
- **Single codebase**: One fetcher supports both Cloud and Server/DC without branching at the tool level
- **API differences**: Jira Cloud uses REST v3; Server/DC uses v2 or hybrid. Confluence Cloud uses v2; Server/DC uses legacy REST.
- **Field naming**: Cloud uses field IDs (e.g., `customfield_10000`); Server/DC uses field keys (e.g., `cf[10000]`). Abstraction hides this.
- **OAuth support**: Both Cloud and Server/DC support OAuth, but token refresh behavior differs.

**Detection:**
- `is_atlassian_cloud_url(url)` checks if URL contains `.atlassian.net` (Cloud indicator)
- `is_cloud` property on fetchers delegates to config
- Conditionals: `if self.is_cloud: ...` or `if self.fetcher.is_cloud: ...`

**Examples:**
```python
# In OAuthConfig.token_url property:
if self.is_data_center and self.base_url:
    return f"{self.base_url.rstrip('/')}{DC_TOKEN_PATH}"
return CLOUD_TOKEN_URL

# In JiraFetcher._construct_search_endpoint():
if self.is_cloud:
    return f"{self.base_url}/rest/api/3/search"
else:
    return f"{self.base_url}/rest/api/2/search"
```

**Evidence:**
- `src/mcp_atlassian/utils/urls.py:is_atlassian_cloud_url()`
- `src/mcp_atlassian/jira/client.py` — `is_cloud` property
- Multiple `if self.is_cloud:` conditionals throughout jira/ and confluence/

---

## 10. Structured Logging with Masking

**Decision:** Use Python's `logging` module with structured context, and mask sensitive data (tokens, credentials) in all log output.

**Rationale:**
- **Debugging**: Structured logs with context (issue key, project, exception) make debugging easier
- **Security**: Tokens and credentials are masked (`••••••`) before being logged, preventing accidental exposure in logs
- **Auditability**: Log levels (INFO, WARNING, ERROR) help identify issues

**Masking targets:**
- OAuth tokens (access_token, refresh_token)
- API tokens (JIRA_API_TOKEN, etc.)
- Basic auth credentials (username:password)
- Custom field values containing sensitive keywords (password, secret, token)

**Entry points:**
- `utils/logging.py:mask_sensitive()` — applies masks to log messages
- Configured in logger setup in `servers/main.py`

**Evidence:**
- `src/mcp_atlassian/utils/logging.py` — masking logic
- `mask_sensitive()` called before logging sensitive data

---

## 11. Dependency Injection via Request Context

**Decision:** Store configuration and fetcher instances in `MainAppContext` (per-request context) and retrieve them via helper functions.

**Rationale:**
- **Testability**: Mock context in unit tests; inject real context in integration tests
- **Thread safety**: Each request has its own context; no shared state
- **Lazy initialization**: Fetchers are created once per request (or cached in context)
- **Type safety**: Context is strongly typed via `MainAppContext` dataclass

**Usage:**
```python
# In a tool handler:
async def jira_get_issue(request: Request, key: str) -> dict:
    fetcher = get_jira_fetcher(request.ctx)
    return fetcher.get_issue(key).to_simplified_dict()
```

**Evidence:**
- `src/mcp_atlassian/servers/context.py` — `MainAppContext` dataclass
- `src/mcp_atlassian/servers/dependencies.py` — helper functions
- All tools in `servers/jira.py` and `servers/confluence.py` retrieve fetchers via context

---

## Known Issues and Deferred Work

### TOR-02-ePsqZQq: Incomplete Test Coverage for Sole Resource with Configured ID

**Issue:** TOR-02-ePsqZQq specifies "resolve to sole resource regardless of whether a cloud ID is configured". The test covers the case where `cloud_id=None` and a sole resource is returned. However, the case where a sole resource matches a configured ID (e.g., configured `xyz789`, sole resource is `xyz789`) is not independently tested.

**Current state:** The underlying logic is correct and trivially verifiable by code inspection (`if len(resources) == 1: self.cloud_id = resources[0]["id"]` overwrites configured ID), and the case is implicitly tested by `TOR-02-MLk6Fcn`. However, explicit independent test coverage would make this more robust.

**Workaround:** Code inspection confirms correctness. Low priority — the resolution logic is simple and the MLk6Fcn test exercises the combined case.

**Follow-up:** Add a standalone test case `test_get_cloud_id_sole_resource_overwrites_configured` that explicitly verifies a sole resource is used even when a different configured ID is set.

### Manual End-to-End Test for Multi-Instance OAuth

**Issue:** The multi-instance OAuth path (token with access to 2+ Atlassian Cloud organizations + `ATLASSIAN_OAUTH_CLOUD_ID` configured to a non-first site) cannot be exercised without an account that has access to multiple Atlassian Cloud organizations.

**Current state:** All TOR requirements are unit-tested. However, a reviewer with a multi-org account should manually run:
```bash
ATLASSIAN_OAUTH_CLOUD_ID=<some-non-first-site-id> uv run mcp-atlassian --oauth-setup
```
And verify: (1) the correct site is selected (not the first), and (2) a wrong configured ID produces the expected actionable error message rather than silently connecting to the wrong site.

**Workaround:** Unit tests cover all code paths. Code inspection of `_get_cloud_id()` verifies correctness.

### Windows Sync Issue: `uv sync` PEP 440 Validation

**Issue:** Running `uv sync --frozen --all-extras --dev` locally on the peakflames fork fails with:
```
ValueError: Version 'X.Y.ZpeakflamesN' does not conform to the PEP 440 style
```

**Root cause:** Fork tags use the pattern `vX.Y.Z-peakflames.N` (e.g., `v0.21.2-peakflames.1`). When `uv sync` runs without `--no-editable`, it triggers hatchling's editable install, which calls `uv-dynamic-versioning`. That tool reads the git tag and tries to convert it to a version string, which fails PEP 440 validation for pre-release suffixes like `-peakflames.N`.

**Workaround:** Always use `uv sync --no-editable --frozen --all-extras --dev`. This bypasses the hatchling editable build entirely and matches the Dockerfile's approach. See CLAUDE.md for details.

**Impact:** Developers working on the fork must remember to add `--no-editable`. The Docker build is unaffected (it already uses `--no-editable`). CI/CD is unaffected.

### Pre-commit mypy Error in `servers/main.py`

**Issue:** mypy reports a pre-existing error on `servers/main.py:363`:
```
error: Missing type parameters for generic type "TTLCache" [type-arg]
```

**Status:** This error existed before Epic 1SVldWi and is unrelated to OAuth validation. It does not block commits or deployments.

**Workaround:** Error is in the main branch; not addressed in this epic.

---

## Future Considerations

1. **Refresh token rotation**: OAuth servers sometimes rotate refresh tokens on token refresh. Consider tracking token age and re-running setup if refresh fails.
2. **Multi-tenant UI**: A settings panel to switch between Cloud sites after OAuth setup (instead of requiring reconfiguration).
3. **Rate limiting**: Add client-side rate limiting (e.g., exponential backoff) for API calls.
4. **GraphQL support**: Jira Cloud's GraphQL API offers more efficient queries for certain operations.
5. **Field caching strategy**: Cache field metadata longer (currently per-request) to reduce API calls.
