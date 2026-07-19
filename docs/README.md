# Developer documentation

This directory documents the implementation and design decisions of Route
Management.

The application is implemented through **Stage 8**, completing the planned
MVP. GPX imports, manual path splits, path-segment deletions, and degree-two
junction merges can be staged, inspected, undone, cancelled, and committed
atomically to SQLite.

The immediate post-MVP junction-move tool is also implemented. It stages one
replacement operation for the junction and all attached path endpoints.

Trace-overlap reconciliation Iterations 0–7 are also implemented: synthetic
measurement fixtures, diagnostics, complete-section reuse, reviewed partial
reuse with branch/join boundary creation, multiple intervals per section, and
connected saved-chain candidates, and conservative automatic reuse with a
cautious review-everything import policy. Iteration 6 currently covers
click-to-place boundary adjustment and server-side replay validation.
Iteration 7 currently covers explicit saved duplicate cleanup for two
single-segment chains.

## Documents

- [Architecture and algorithms](architecture.md) — system structure, data flow,
  persistence model, geometry processing, edit-session replay, and design
  trade-offs.
- [Implementation plan](../plans/walking-path-network.md) — staged delivery
  roadmap and product requirements.
- [Trace overlap reconciliation plan](../plans/trace-overlap-reconciliation.md)
  — iterative map matching, review, and saved-path reuse for approximate GPX
  overlaps.

## Quick development reference

```bash
source .venv/bin/activate
flask --app app run --debug
```

Run the test suite:

```bash
python -m pytest
```

Initialize or reset an empty database through the Flask CLI:

```bash
flask --app app init-db
```

Insert the small development network:

```bash
flask --app app seed-network
```
