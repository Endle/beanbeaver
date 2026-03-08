# Pixi And Rust Migration Draft

## Status

This is a draft proposal for moving the project away from Nix as the primary
developer environment and toward:

- `pyproject.toml` as the Python package source of truth
- `pixi` as the primary environment/task manager
- `cargo` + `maturin` as the Rust/PyO3 build path

The intent is to support both current Python development and a foreseeable
rewrite of selected components in Rust.

## Why Change

The current repository has multiple dependency definitions:

- `flake.nix`
- `pyproject.toml`
- GitHub Actions `pip install ...` commands

This creates drift. Today, `flake.nix` includes runtime dependencies that are
not fully represented in `pyproject.toml`, while CI mostly bypasses Nix and
installs Python packages directly.

That shape is workable for a solo maintainer, but it is not a strong long-term
foundation if we want:

- simpler contributor onboarding
- one clear dependency contract
- an easier path to mixed Python/Rust development

## Recommendation

Adopt the following model:

- `pyproject.toml` defines Python package metadata and Python dependencies
- `Cargo.toml` defines Rust crates and native build configuration
- `maturin` builds and installs PyO3 extensions
- `pixi.toml` defines the dev environment, lockfile, and common tasks

This keeps responsibilities clear:

- Python packaging stays in Python-native metadata
- Rust packaging stays in Rust-native metadata
- Pixi manages reproducible environments and task execution
- Pixi does not replace `cargo` or `maturin` as the actual Rust build path

## Non-Goal

Do not use Pixi's Rust build backend as the primary production build path for
PyO3 modules at this stage. It is better to keep `cargo` + `maturin` as the
source of truth for native extension builds.

## Why Pixi Fits Better Than Nix For This Repo

### 1. The project is primarily a Python application

The current codebase is still mostly standard Python:

- CLI commands
- application orchestration
- receipt parsing
- importers
- tests and linting

That means Python-native metadata should be authoritative.

### 2. Rust/PyO3 is additive, not the whole system

The likely future is not "rewrite everything in Rust". It is "rewrite selected
hot paths or hard-to-maintain logic in Rust while keeping the application
surface in Python".

That is exactly the kind of setup where:

- Python remains the host application
- Rust provides focused extension modules
- `maturin develop` is a practical local workflow

### 3. Pixi can unify Python and Rust toolchains without replacing them

A Pixi environment can provide:

- `python`
- `rust`
- `cargo`
- `maturin`
- `ruff`
- `mypy`
- `pytest`
- project runtime dependencies

That gives one entry point for local development without forcing the repo into
Nix-first workflows.

## Proposed Target Layout

### Python

- `pyproject.toml`
  - core runtime dependencies
  - optional dependency groups such as `dev`, `test`, and possibly `server`
  - package entrypoints such as `bb`

### Rust

- `Cargo.toml`
- `src/` or `rust/` for Rust crates
- PyO3 extension modules exposed under the `beanbeaver` Python package

### Environment

- `pixi.toml`
  - pinned Python version
  - pinned Rust toolchain
  - dependencies installed from Conda/PyPI as appropriate
  - tasks for linting, testing, local extension builds, and CLI runs

## Suggested Dependency Strategy

### `pyproject.toml` should become complete

At minimum, Python metadata should include all real runtime dependencies. Based
on the current codebase, that likely includes packages such as:

- `beancount`
- `httpx`
- `pillow`
- `fastapi`
- `uvicorn`
- `pandas`

Potential split:

- base runtime deps for normal CLI usage
- `server` extra for `bb serve`
- `test` extra for `pytest`
- `dev` extra for `ruff`, `mypy`, and `maturin`

The exact split should optimize for maintainability, not theoretical purity.
If optional groups add too much complexity, a small number of clear groups is
better than a highly fragmented model.

### `pixi.toml` should manage the developer workflow

Pixi should provide the standard entry points:

- `pixi run lint`
- `pixi run test`
- `pixi run test-e2e-cached`
- `pixi run serve`
- `pixi run bb -- --help`
- `pixi run maturin-develop`

The lockfile should give reproducibility without making contributors learn Nix.

## Rust/PyO3 Direction

The first Rust rewrites should target components with one or more of these
properties:

- performance-sensitive parsing or matching logic
- logic that is algorithmically dense but side-effect-light
- logic with stable typed inputs and outputs
- logic already covered by strong tests

This repo's architecture already suggests good candidates:

- receipt parsing subcomponents
- matching logic
- normalization helpers

Bad first candidates:

- CLI wiring
- filesystem-heavy runtime modules
- ledger I/O
- web server orchestration

These are not good PyO3 wins and would add integration complexity too early.

## Architectural Rule For Rust Rewrites

Rust components should preserve the existing trust boundaries:

- keep ledger I/O in `ledger_access/`
- keep orchestration in `application/`, `cli/`, and `runtime/`
- move only pure or mostly-pure computation into Rust first

That means PyO3 modules should generally accept plain data and return plain
data, rather than performing direct filesystem or ledger operations.

This matches the existing trust-zone policy and lowers migration risk.

## Rust Scope Rule

Rust code exposed through PyO3 will only be used for parser rules.

The default contract for Rust parser code is:

- pure-function style inputs and outputs
- deterministic behavior
- no direct ledger access
- no orchestration responsibilities
- no network, subprocess, or arbitrary OS interaction

Tolerated exceptions:

- structured logging/syslog
- file I/O only for explicitly designated paths at narrow boundaries

Design implication:

- the core Rust parser logic should remain side-effect-light and testable
- if logging or designated-path file I/O is needed, keep it in thin boundary
  adapters instead of mixing it into the parsing core
- Rust code and its dependencies should not become a hidden syscall-heavy
  runtime layer

This rule is intended to keep Rust focused on computational logic and prevent a
future drift into rewriting orchestration or platform-facing runtime code in
PyO3 modules.

## Migration Plan

### Phase 1: Make Python packaging truthful

1. Update `pyproject.toml` so it lists all real runtime dependencies.
2. Add clear optional dependency groups for dev/test/server if useful.
3. Ensure a clean local setup works without Nix.
4. Update README installation instructions accordingly.

Success criterion:

- a contributor can install and run tests using standard Python tooling alone

### Phase 2: Introduce Pixi alongside Nix

1. Add `pixi.toml`.
2. Mirror the common dev commands as Pixi tasks.
3. Keep `flake.nix` temporarily during transition.
4. Validate Linux/macOS/Windows workflows.

Success criterion:

- daily development works through `pixi run ...`

### Phase 3: Move CI toward the new source of truth

1. Stop hardcoding ad hoc `pip install ...` lists in workflows.
2. Use either:
   - Pixi in CI directly, or
   - Python install from `pyproject.toml` plus explicit extras
3. Keep CI consistent with local development.

Success criterion:

- CI dependencies come from the same metadata used by contributors

### Phase 4: Add Rust toolchain support

1. Add `Cargo.toml` and a minimal PyO3 extension.
2. Add `maturin develop` task through Pixi.
3. Add tests that validate Python-to-Rust integration boundaries.
4. Keep the first extension small and isolated.

Success criterion:

- one production-relevant component can be built and exercised locally through
  the standard dev workflow

### Phase 5: Rewrite selected pure components

Start with one narrow, test-covered component. Do not start with large runtime
surfaces.

Preferred process:

1. lock down Python behavior with tests
2. implement Rust version behind a narrow interface
3. compare outputs
4. switch callers after parity is proven

## Risks

### Incomplete dependency migration

If `pyproject.toml` remains incomplete, Pixi will only hide the same problem
that Nix hides today. The first job is to make Python metadata accurate.

### Overusing Rust too early

If Rust rewrites target orchestration or I/O-heavy modules first, the project
will pay integration cost without receiving clear maintainability or
performance gains.

### Too many optional dependency groups

An overdesigned package matrix can become harder to maintain than a small,
honest superset.

### CI drift

If local development moves to Pixi but CI keeps manual `pip install` commands,
the project will still have multiple dependency truths.

## Practical Near-Term Decision

The recommended near-term path is:

1. keep `flake.nix` temporarily
2. make `pyproject.toml` complete
3. add `pixi.toml`
4. move daily development to Pixi
5. add Rust/PyO3 support through `cargo` + `maturin`
6. remove Nix only after the new workflow is stable

## Open Questions

- Should `bb serve` dependencies live in the base install or a `server` extra?
- Should importer dependencies such as `pandas` be base dependencies or an
  importer-specific extra?
- Should Rust code live in a top-level `rust/` directory or in the package root
  with standard `maturin` layout?
- Do we want `abi3` wheels eventually, or is local/source build enough for the
  foreseeable future?

## Conclusion

Switching from Nix to Pixi is reasonable for this project, including the
foreseeable PyO3 direction.

The key condition is discipline:

- Python dependencies must move into `pyproject.toml`
- Rust builds must stay grounded in `cargo` + `maturin`
- Pixi should unify environment management, not become a second build system

If that discipline is maintained, Pixi gives a cleaner path than the current
Nix-centered setup.
