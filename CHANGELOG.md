# Changelog

This file tracks fork-specific releases of `peakflames/mcp-atlassian`.
Fork tags follow the pattern `vX.Y.Z-peakflames.N` and publish Docker images
to `peakflames/mcp-atlassian` (see `.github/workflows/docker-publish.yml`).
Upstream history is tracked separately in `sooperset/mcp-atlassian`.

## Unreleased

### Fixes

- `confluence_get_labels`, `confluence_get_attachments`,
  `confluence_download_attachment`, `confluence_download_content_attachments`,
  and `confluence_get_page_images` now enforce `CONFLUENCE_SPACES_BLOCKED`.
  The content's space is checked before labels, attachment metadata, or file
  contents are returned.
- `confluence_get_comments` and `confluence_get_space_page_tree` now enforce
  `CONFLUENCE_SPACES_BLOCKED`.
- `confluence_get_page_children`, `confluence_get_page_history`,
  `confluence_get_page_diff`, and `confluence_get_page_views` now enforce
  `CONFLUENCE_SPACES_BLOCKED`. The space of the page (for
  `confluence_get_page_children`, the parent page) is checked before child
  pages, page versions, or view statistics are read, and the request is
  denied when that space cannot be determined while a block list is set.
- `confluence_add_label`, `confluence_upload_attachment`,
  `confluence_upload_attachments`, and `confluence_delete_attachment` now
  enforce `CONFLUENCE_SPACES_BLOCKED` and `CONFLUENCE_SPACES_READONLY` for the
  target content.
- `confluence_reply_to_comment` now enforces `CONFLUENCE_SPACES_BLOCKED` and
  `CONFLUENCE_SPACES_READONLY`, based on the space of the page or blog post
  the comment belongs to.
- Write tools that take a page ID (`confluence_update_page`,
  `confluence_delete_page`, `confluence_move_page`, `confluence_add_comment`,
  and `confluence_add_label`) now enforce `CONFLUENCE_SPACES_BLOCKED` and
  `CONFLUENCE_SPACES_READONLY` for the page's space, and are denied when that
  space cannot be determined while either list is set.
- `confluence_create_page`, `confluence_update_page` (with `parent_id`), and
  `confluence_move_page` (with `target_parent_id`) also check the parent's
  space against `CONFLUENCE_SPACES_BLOCKED` and `CONFLUENCE_SPACES_READONLY`,
  and are denied when it cannot be determined while either list is set. A
  folder as the parent resolves to the folder's space, including under Cloud
  OAuth.
- `confluence_get_page` now fails closed when a block list is set and the
  page's space cannot be determined. When called with `title` and
  `space_key`, it enforces `CONFLUENCE_SPACES_BLOCKED` for both the requested
  space key and the space of the page that is returned.
- Space keys are compared against `CONFLUENCE_SPACES_BLOCKED` and
  `CONFLUENCE_SPACES_READONLY` ignoring case and surrounding whitespace, the
  same way the lists themselves are read.
- `CONFLUENCE_SPACES_BLOCKED` and `CONFLUENCE_SPACES_READONLY` now apply to
  requests authenticated with the `X-Atlassian-Confluence-Url` and
  `X-Atlassian-Confluence-Personal-Token` headers. The write-access check on
  page-ID tools now also checks the server's lists directly.

### Behaviour changes

- Content-level writes (labels, attachments, comment replies) now also
  enforce `CONFLUENCE_SPACES_READONLY` when only a read-only list is set.
- Writes are denied when `CONFLUENCE_SPACES_READONLY` is set and the target's
  space cannot be determined, even if no block list is set.

### Notes

- With no space lists configured, these checks make no extra requests.
- The space lists come from the server's own Confluence configuration. A
  server with no global Confluence configuration, used only with
  header-based credentials, has no lists to enforce.
- With a block list set, a request is denied when the content's space cannot
  be determined. The error names `CONFLUENCE_SPACES_BLOCKED`.
- Under Cloud OAuth, the space check needs the `read:space:confluence` scope
  plus read access to the content (page, blog post, folder, attachment, or
  comment).
  Without them, checked requests are denied while a relevant list is set.
- Embed IDs passed to the label and attachment tools now return the
  access-control error instead of a 404 when their space is blocked or cannot
  be determined.

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
