@AGENTS.md

---

## Fork-specific notes (peakflames/mcp-atlassian)

### Branch / tag strategy

- `peakflames/main` — fork's default branch; receives feature merges
- `upstream/main` (`sooperset/mcp-atlassian`) — tracks the original project
- Feature branches merge into `peakflames/main` for daily use
- Long-lived feature branches (e.g. `feature/per-project-access-control`) are kept
  undeleted so they can eventually be submitted as upstream PRs
- To contribute back upstream: create a `contrib/` branch off `upstream/main`,
  cherry-pick the relevant commit(s), open a PR to `sooperset/mcp-atlassian`

### Version tags

Fork tags follow the pattern `vX.Y.Z-peakflames.N` (e.g. `v0.21.2-peakflames.1`).
The Docker publish workflow uses `type=semver` metadata which treats the pre-release
suffix as a semver pre-release — Docker tags like `0.21.2-peakflames.1` are pushed,
but `latest`, `0.21`, and `0` tags are NOT generated (semver pre-releases are excluded
from those alias tags).

### `uv sync` PEP 440 issue

Running `uv sync --frozen --all-extras --dev` locally will **fail** with:

```
ValueError: Version 'X.Y.ZpeakflamesN' does not conform to the PEP 440 style
```

**Why:** `uv sync` without `--no-editable` triggers hatchling's editable install,
which calls `uv-dynamic-versioning`. That tool reads the git tag, converts it to
a version string, and rejects `vX.Y.Z-peakflames.N` because it is not PEP 440.

**Fix — always add `--no-editable`:**

```bash
uv sync --frozen --all-extras --dev --no-editable
```

This matches exactly what the Dockerfile does and bypasses the hatchling editable
build entirely. Note: with `--no-editable` you must re-run `uv sync` after editing
source files to pick up changes (or use `uv run` which handles this automatically).

The Docker build is unaffected because both sync steps already use `--no-editable`.
