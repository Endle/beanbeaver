Trust Zones

This document defines runtime trust boundaries in this repository.
The goal is simple: keep sensitive operations isolated and keep business logic testable.

Zones
- `Privileged`
  - May read/write ledger files and perform high-impact data access.
- `Orchestrator`
  - Coordinates workflows, user interaction, filesystem operations, and service calls.
- `Pure`
  - Deterministic logic and data transformation.
  - syslog is tolerated
  - date.today is tolerated. We may fix it in future

Current Directory Mapping
- `Privileged`
  - `ledger_access/`
- `Orchestrator`
  - `cli/`
  - `application/`
  - `runtime/`
  - `importers/`
- `Pure`
  - `domain/`
  - `receipt/`
  - `rules/` (data/config only)
  - `util/`
- Tooling, tests, and metadata are not part of runtime trust zoning:

Dependency Rules
- `Pure` may import only `Pure`.
- `Orchestrator` may import `Orchestrator`, `Pure`, and `Privileged`.
- `Privileged` may import only `Privileged` and `Pure`.
- Violations are enforced in CI by `tests/test_trust_zone_boundaries.py`.

Inheritance Rules
- Subdirectories inherit the nearest parent zone unless explicitly documented.
- Explicit subdirectory or file-level classification overrides inheritance.

Contributor Checklist
- New ledger access belongs in `ledger_access/` unless there is a documented exception.
- Keep orchestration and side effects in Orchestrator modules.
- Keep domain logic in Pure modules and pass data in via function arguments.
- If a new directory is added, classify it here.
- If a new `src/*.rs` module is added, classify it under Native Extension Module Mapping.

Native Extension Module Mapping (PyO3)

The receipt and matching logic lives in the `_rust_matcher` native extension
(`src/*.rs`), reached from Pure/Orchestrator Python modules through the thin
loader in `receipt/_rust.py`. The same three zones and dependency rules apply to
the crate's internal `use crate::...` graph, enforced from
`tests/test_trust_zone_boundaries.py` by static analysis of `crate::` edges.

- `Privileged`
  - `python_ledger_access.rs`
- `Orchestrator`
  - `match_service.rs`
  - `python_match_service.rs`
- `Pure`
  - all other src/*.rs logic modules and their python_* bindings (default)
- `Excluded`
  - `lib.rs`
  - `main.rs`
  - `tui/` (the `bb-tui` app; all `src/tui/*.rs` submodules)

`Excluded` modules are extension/binary bootstrap (module registration, the CLI
entry point, and the `bb-tui` app) and are exempt like tooling and tests. Any
`src/*.rs` not listed above defaults to `Pure`, so new pure logic needs no doc
change while a new Privileged/Orchestrator module must be declared here.
