# Changelog

This file tracks fork-specific releases of `peakflames/mcp-atlassian`.
Fork tags follow the pattern `vX.Y.Z-peakflames.N` and publish Docker images
to `peakflames/mcp-atlassian` (see `.github/workflows/docker-publish.yml`).
Upstream history is tracked separately in `sooperset/mcp-atlassian`.

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
