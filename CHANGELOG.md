# Changelog

This file tracks fork-specific releases of `peakflames/mcp-atlassian`.
Fork tags follow the pattern `vX.Y.Z-peakflames.N` and publish Docker images
to `peakflames/mcp-atlassian` (see `.github/workflows/docker-publish.yml`).
Upstream history is tracked separately in `sooperset/mcp-atlassian`.

## Unreleased

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
