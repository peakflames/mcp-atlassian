# Requirements Baseline

Formal Gherkin-style requirements (`.feature.md`), their traceability sidecars (`.feature.tracing.json`), and the Change Records (`changes/*.toml`) that explain each change to a merged requirement.

## Conventions

- Feature numbers (`NN`) are stable once assigned and never reused.
- TOR IDs (`TOR-NN-XXXXXXX`) are **immutable** once merged to develop — they are foreign keys
  referenced by epic specs, tests, and handoffs — and are never reused.
- A change in meaning gets a **new ID**: a `supersede` Change Record names the successor. A
  requirement nobody needs any more is retired with a `retire` record. Only a same-meaning
  rewording with passing test evidence keeps its ID (a `reword` record).
- Change Records (`changes/*.toml`) are history: written once, never edited.
- Every change travels through a `docs/{task-short-name}` branch via
  `/peak-workflow:capture-requirements`; merging the branch is the approval.
- `docs/.peak-workflow.toml` holds the project's format (kept by `/peak-workflow:setup` and
  `/peak-workflow:upgrade` — do not edit it) and `[lifecycle] disputed_threshold_days`, how long
  a requirement may stay disputed before it blocks work (default 30; `# Safety` requirements: 0).
