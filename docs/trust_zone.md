Trust Zones

This document defines runtime trust boundaries in `vendor/beanbeaver`.
The goal is simple: keep sensitive operations isolated and keep business logic testable.

Zones
- `Privileged`
  - May read/write ledger files and perform high-impact data access.
- `Orchestrator`
  - Coordinates workflows, user interaction, filesystem operations, and service calls.
- `Pure`
  - Deterministic logic and data transformation.
  - syslog is tolerated

Current Directory Mapping
- `Privileged`
  - `vendor/beanbeaver/ledger_reader/`
- `Orchestrator`
  - `vendor/beanbeaver/cli/`
  - `vendor/beanbeaver/application/`
  - `vendor/beanbeaver/runtime/`
  - `vendor/beanbeaver/importers/`
- `Pure`
  - `vendor/beanbeaver/domain/`
  - `vendor/beanbeaver/receipt/`
  - `vendor/beanbeaver/receipt/rules/` (data/config only)
  - `vendor/beanbeaver/util/`
- Tooling, tests, and metadata are not part of runtime trust zoning:

Inheritance Rules
- Subdirectories inherit the nearest parent zone unless explicitly documented.
- Explicit subdirectory or file-level classification overrides inheritance.

Contributor Checklist
- New ledger access belongs in `ledger_reader/` unless there is a documented exception.
- Keep orchestration and side effects in Orchestrator modules.
- Keep domain logic in Pure modules and pass data in via function arguments.
- If a new directory is added, classify it here.
