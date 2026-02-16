# Runtime vs Util

This directory (`beanbeaver.runtime`) is intentionally different from `beanbeaver.util`.

## `beanbeaver.runtime` (stateful/runtime infrastructure)

Use this package for process-level concerns such as:
- logging configuration
- environment-driven behavior
- filesystem/project path resolution
- singleton-like runtime services

These modules are allowed to depend on application context and runtime state.

## `beanbeaver.util` (pure/stateless helpers)

`beanbeaver.util` is for pure helpers only. Modules there should:
- avoid application state
- avoid reading environment/filesystem/network/process state
- avoid importing `beancount`
- avoid importing `beanbeaver.*` modules

In short:
- `runtime` = stateful infrastructure
- `util` = pure, dependency-light helper logic
