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
  - `receipt/rules/` (data/config only)
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
