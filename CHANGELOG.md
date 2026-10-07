# Changelog

This file tracks fork-specific releases of `peakflames/mcp-atlassian`.
Fork tags follow the pattern `vX.Y.Z-peakflames.N` and publish Docker images
to `peakflames/mcp-atlassian` (see `.github/workflows/docker-publish.yml`).
Upstream history is tracked separately in `sooperset/mcp-atlassian`.

## Unreleased

### Fixes

- Fix `confluence_get_page_children` under Cloud OAuth: children are listed via
  the v2 `/api/v2/pages/{id}/direct-children` endpoint (keeping `page` and,
  with `include_folders`, `folder` items) instead of the v1
  `/rest/api/content/{id}/child/{type}` endpoints, which the gateway is
  removing (upstream issue #1598). The list is read 250 items per request,
  up to 20 requests; if that is not enough to reach `start + limit` matching
  items, the tool returns an error instead of a partial list. When `expand`
  includes `version` or `body`, child pages are looked up via
  `/api/v2/pages?id=...`, 250 IDs per request. On this path `start`/`limit`
  apply to pages and folders together, other `expand` fields are ignored,
  and folders carry no version. Server/Data Center and
  non-OAuth Cloud still use v1 (`src/mcp_atlassian/confluence/pages.py`,
  `src/mcp_atlassian/confluence/v2_adapter.py`). Requires the
  `read:hierarchical-content:confluence` scope, plus `read:page:confluence`
  for versions and content.
- `confluence_get_page_children` no longer reports a failed lookup as an
  empty list of children. The fetcher raises, and the tool returns an
  `error` object (`Page not found or not accessible: <id>` on 404; error
  text never includes the request URL); 401/403 is reported as an
  authentication failure
  (`src/mcp_atlassian/confluence/pages.py`,
  `src/mcp_atlassian/servers/confluence.py`).

## v0.21.2-peakflames.5

### Fixes

- Fix Confluence attachment downloads under Cloud OAuth: relative
  `_links.download` values are now resolved against the Atlassian API gateway
  (plus `/wiki`) instead of the site URL, which rejects OAuth bearer tokens.
  Affects `confluence_download_attachment`,
  `confluence_download_content_attachments`, and `confluence_get_page_images`
  (`src/mcp_atlassian/confluence/client.py`,
  `src/mcp_atlassian/servers/confluence.py`,
  `src/mcp_atlassian/confluence/attachments.py`). Same gateway approach as
  upstream #1580, but the `/download/attachments/...` path is kept rather than
  rewritten to the v1 `/rest/api/content/.../download` endpoint, because the
  gateway is removing v1 content endpoints (upstream issue #1598). This
  download path has not yet been confirmed live with an OAuth bearer token.
- Fix `confluence_get_labels` under Cloud OAuth: labels are read via the v2
  `pages` / `blogposts` / `attachments` label endpoints, because the v1
  `/rest/api/content/{id}/label` endpoint is no longer served through the
  gateway (upstream issue #1598) (`src/mcp_atlassian/confluence/labels.py`,
  `src/mcp_atlassian/confluence/v2_adapter.py`). Requires the
  `read:label:confluence` scope; OAuth apps with only classic scopes get a 401.
- Report Smart Link embeds instead of a bare 404 under Cloud OAuth: when
  `confluence_get_page`, `confluence_get_attachments`, or
  `confluence_get_labels` gets a 404 for a non-attachment content ID, the ID is
  checked against `/api/v2/embeds/{id}`. `confluence_get_page` and
  `confluence_get_attachments` return the embed's title and `embedUrl`;
  `confluence_get_labels` raises an error that includes the `embedUrl`.
  Attachment IDs (`att` prefix) and `confluence_download_attachment` are not
  checked. Embeds in a space listed in `CONFLUENCE_SPACES_BLOCKED` are not
  reported; if a block list is set and the embed's space cannot be resolved,
  the original error is returned. Requires the `read:embed:confluence` scope,
  plus `read:space:confluence` when a block list is set; without them the
  original error is returned (`src/mcp_atlassian/confluence/client.py`,
  `src/mcp_atlassian/servers/confluence.py`,
  `src/mcp_atlassian/confluence/attachments.py`,
  `src/mcp_atlassian/confluence/labels.py`).
- Replace a site-specific cloud ID in docs and tests with a placeholder.

### Known limitations

- `confluence_add_label` still uses the v1 POST endpoint and fails under Cloud
  OAuth; v2 has no label-create endpoint.

## v0.21.2-peakflames.4

### Features

- Resolve `@`-mentions when writing Jira Cloud comments/descriptions: the
  Markdown → ADF converter now turns `@[Display Name]` and `[~identifier]`
  tokens into real ADF `mention` nodes, resolving the identifier via
  display name → email → account ID (`_get_account_id`). An explicit
  `[~accountid:<id>]` token bypasses lookup. Unresolved mentions are left
  as literal text rather than producing a broken tag
  (`src/mcp_atlassian/models/jira/adf.py`, `src/mcp_atlassian/jira/client.py`).

### Fixes

- Fix Jira attachment upload: `upload_attachment` now uploads via
  `add_attachment_object` using the open file handle and the file's
  basename as the multipart filename. Previously it passed the full
  absolute path as the filename (and discarded a redundantly opened
  handle), so uploads were stored under the filesystem path or rejected.
  The real attachment ID is now extracted from the array response
  (`src/mcp_atlassian/jira/attachments.py`).

## v0.21.2-peakflames.3

### Features

- Resolve and validate the OAuth cloud site for multi-instance tokens, so a
  single OAuth token spanning multiple Atlassian sites resolves to the correct
  cloud id (`src/mcp_atlassian/utils/oauth.py`).

### Fixes

- Return complete Jira issue fields: pass the `*all` sentinel directly and
  filter null custom fields, and revert `DEFAULT_READ_JIRA_FIELDS` to the
  upstream 10-field set (`src/mcp_atlassian/jira/issues.py`,
  `src/mcp_atlassian/models/jira/issue.py`, `src/mcp_atlassian/exceptions.py`).

### Docs

- Refresh architecture and design notes to the as-built state and add the
  implementation-plan docs for the Jira field fixes.

## v0.21.2-peakflames.2

### Docs

- Add Atlassian Cloud OAuth proxy deployment guide
  (`docs/guides/atlassian-cloud-oauth.mdx`) covering three known issues when
  deploying against Atlassian Cloud with `ATLASSIAN_OAUTH_PROXY_ENABLE=true`:
  - RFC 8707 `resource` param causing token exchange to fail with
    `invalid_target: Incorrect resource parameters` (sooperset#1137), including
    a sed-patch workaround with a build-time grep assertion.
  - `confluence_get_space_page_tree` hitting the removed v1 API and returning
    410 Gone (sooperset#1323), documenting the `ENABLED_TOOLS` exclusion.
  - fastmcp defaulting to in-memory OAuth proxy storage on Linux, wiping client
    registrations and JTIs on every pod restart.
  - Required Atlassian Developer Console configuration (callback URLs, classic
    and granular Confluence scopes).
- Add `examples/oauth_storage.py`, a stdlib-only async file store factory that
  persists OAuth proxy state to the volume-mounted `.mcp-atlassian` directory
  with a `/tmp` fallback.

_Contributed by Deniel Moraes (#1)._

## v0.21.2-peakflames.1

- Add fork-specific notes to `CLAUDE.md` (tags, branch strategy, `uv sync`
  PEP 440 workaround).

## v0.21.1-peakflames.1

- First fork release. Adds per-project and per-space `BLOCKED`/`READONLY`
  access controls, restores Docker Hub publishing for the fork, and removes the
  upstream PyPI publish workflow.
