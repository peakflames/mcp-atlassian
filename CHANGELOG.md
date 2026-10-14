# Changelog

This file tracks fork-specific releases of `peakflames/mcp-atlassian`.
Fork tags follow the pattern `vX.Y.Z-peakflames.N` and publish Docker images
to `peakflames/mcp-atlassian` (see `.github/workflows/docker-publish.yml`).
Upstream history is tracked separately in `sooperset/mcp-atlassian`.

## Unreleased

## v0.21.2-peakflames.9

### Features

- Add `jira_move_issues_to_board` and `jira_move_issues_to_backlog`. They move
  up to 50 issues from a board's backlog onto the board
  (`POST /rest/agile/1.0/board/{id}/issue`) and back
  (`POST /rest/agile/1.0/backlog/{id}/issue`). On a team-managed Kanban board
  with the backlog enabled, an issue created through the API lands in the
  backlog, and no tool could put it on the board before. Both report the moved
  issues and any per-issue failures; HTTP errors are raised. `issue_keys` goes
  through the per-project write guard. Under OAuth both need
  `write:board-scope:jira-software` (`src/mcp_atlassian/jira/boards.py`).
  On a team-managed board the move does not change the issue's status.

## v0.21.2-peakflames.8

### Fixes

- Fix `confluence_get_comments` under Cloud OAuth. Comments are listed from
  `GET /api/v2/pages/{id}/footer-comments`, with each comment's replies from
  `GET /api/v2/footer-comments/{id}/children`, depth first. Replies carry
  `parent_comment_id`. The v1 child comment listing returns 410 Gone under
  OAuth, and every error was returned as an empty list; listing errors now
  surface as tool errors (`src/mcp_atlassian/confluence/comments.py`,
  `src/mcp_atlassian/confluence/v2_adapter.py`). The listing needs the
  `read:comment:confluence` scope.
- v2 comments carry timestamps: `updated` is the version's `createdAt`, and
  `created` is set for comments at version 1.
- Fix `confluence_get_page` by `title` and `space_key` under Cloud OAuth. The
  page is found with `GET /api/v2/pages?space-id=&title=` instead of the v1
  content lookup, which returns 410 Gone and was reported as "not found"
  (`src/mcp_atlassian/confluence/pages.py`).
- Page ancestors are read with `GET /api/v2/pages/{id}/ancestors` under Cloud
  OAuth, root first as before. Folders are skipped. The endpoint needs the
  `read:content.metadata:confluence` scope; no tool calls this method today.
- `confluence_get_page_views` returns an error when the analytics request
  fails, instead of reporting 0 views. Authentication errors still propagate.
  Under Cloud OAuth the page title is read through the v2 API. The analytics
  endpoint needs the classic `read:confluence-content.summary` scope
  (`src/mcp_atlassian/confluence/analytics.py`).

## v0.21.2-peakflames.7

### Fixes

- Fix `confluence_get_space_page_tree` under Cloud OAuth. The tree is read from
  the v2 `/api/v2/spaces/{id}/pages` listing instead of the v1 space content
  listing, which returns 410 Gone under OAuth. Hierarchy is derived from
  `parentId`, so no per-page ancestor calls are made. The v2 listing has no
  position field: `position` is `None` on this path and pages sort by depth,
  then title. Non-OAuth paths are unchanged
  (`src/mcp_atlassian/confluence/pages.py`).
- Fix `confluence_get_page_history` and `confluence_get_page_diff` under Cloud
  OAuth. Each version is read with
  `GET /api/v2/pages/{id}?version=N&body-format=storage`. The previous lookup
  through `/api/v2/pages/{id}/versions` and `/api/v2/versions/{id}` failed with
  "Version N not found" on every call. An unknown version is a 404. The returned
  dict keeps the v1-compatible shape
  (`src/mcp_atlassian/confluence/pages.py`,
  `src/mcp_atlassian/confluence/v2_adapter.py`).
- Fix `confluence_reply_to_comment` under Cloud so replies appear on the page.
  A v1 reply now names the parent comment's container, read with
  `GET rest/api/content/{id}?expand=container`, as its container and the parent
  comment as its only ancestor. If the parent's container cannot be read,
  nothing is posted (`src/mcp_atlassian/confluence/comments.py`).
- `confluence_reply_to_comment` under Cloud replies only under a page or blog
  post container. A parent whose container is a comment previously received a
  reply that Cloud left off the page while the tool reported success. That
  reply now fails without posting.
- Surface HTTP errors from the Jira agile board lookup. `get_all_agile_boards`
  re-raises instead of returning `[]`, so an authentication error reaches the
  caller (`src/mcp_atlassian/jira/boards.py`).

### Behaviour changes

- `get_all_agile_boards` raises HTTP errors on every auth type. Previously they
  were returned as an empty board list. Under Cloud OAuth, a token without the
  Jira Software scopes now gets the 401 as an error.

### Tests

- Add unit tests for the Cloud OAuth paths above in
  `tests/unit/confluence/test_pages.py`, `test_page_version_v2.py`,
  `test_comment_reply_v1.py`, `test_v2_adapter.py`, and
  `tests/unit/jira/test_boards.py`.

## v0.21.2-peakflames.6

### Fixes

- Fix `confluence_get_page_children` under Cloud OAuth: children are listed via
  the v2 `/api/v2/pages/{id}/direct-children` endpoint (keeping `page` and,
  with `include_folders`, `folder` items) instead of the v1
  `/rest/api/content/{id}/child/{type}` endpoints, which the gateway is
  removing (upstream issue #1598). A folder ID given as the parent is listed
  via `/api/v2/folders/{id}/direct-children`. The list is read 250 items per
  request, up to 20 requests; if that is not enough to reach `start + limit`
  matching items, the tool returns an error instead of a partial list. When
  `expand` includes `version` or `body`, child pages are looked up via
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
- `JIRA_PROJECTS_BLOCKED` and `JIRA_PROJECTS_READONLY` now apply to requests
  authenticated with the `X-Atlassian-Jira-Url` and
  `X-Atlassian-Jira-Personal-Token` headers, so `jira_get_issue` and
  `jira_search` honour them for those requests as they do for other
  credentials (`src/mcp_atlassian/servers/dependencies.py`).
- Jira per-project write access control (`JIRA_PROJECTS_BLOCKED` /
  `JIRA_PROJECTS_READONLY`) now checks every project a write tool touches
  (`src/mcp_atlassian/utils/decorators.py`):
  - `jira_batch_create_issues`: each item in `issues`, including a `project`
    field set on the item.
  - `jira_link_to_epic`: both `issue_key` and `epic_key`.
  - `jira_add_issues_to_sprint`: each key in `issue_keys`.
  - `jira_create_issue`, `jira_update_issue`, `jira_transition_issue`:
    `project`, `parent` and epic-link (`epicKey`, `epic_link`, `epicLink`,
    `Epic Link`) entries in `fields` / `additional_fields`.
  - While either list is set, a reference whose project cannot be determined
    (a numeric issue ID, or a `project` given by ID or by any value that is
    not a project key) is rejected.
  - Project and issue keys are compared after trimming surrounding
    whitespace.
- Tests now check that every argument name the guard reads exists on the
  write tools it is meant for, and that key-bearing parameters on Jira write
  tools are read by the guard.

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
- With no project lists configured, no extra requests are made.
- The project lists come from the server's own Jira configuration. A server
  with no global Jira configuration, used only with header-based
  credentials, has no lists to enforce.

### Tests

- Add HTTP-level tests for Confluence comment writes. Under Cloud OAuth,
  `confluence_add_comment` and `confluence_reply_to_comment` post to the v2
  `/api/v2/footer-comments` endpoint (`pageId` for a new comment,
  `parentCommentId` for a reply, `storage` body). Server/Data Center
  (including Data Center OAuth) and Cloud with an API token stay on v1
  `/rest/api/content`. The tests also cover v2 response mapping, a v2 HTTP
  error returned as JSON by the tool, and read-only mode blocking both tools
  before any request (`tests/unit/confluence/test_comments_footer_v2.py`).
  No runtime change: the v2 routing already shipped with upstream #1070.

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
