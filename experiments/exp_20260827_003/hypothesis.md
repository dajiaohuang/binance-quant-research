# exp_20260827_003 — DIRECT_FROZEN_LAUNCHER_V1

## Observation

`exp_20260827_002` never reached its frozen wrapper because an external
orchestrator selected `[0]` from a scalar PowerShell string and launched only
the first character. It produced no reservation, request, or data result.

## Falsifiable hypothesis

A short, independently frozen PowerShell launcher containing one literal
native invocation of the v6 wrapper and all seven wrapper bindings can remove
runtime command-file discovery, indexing, pipeline, evaluation, and dot-source
behavior while preserving the v5 parser, collector, loader, five-request plan,
and semantic ceiling value-for-value.

## Single primary change

Add a self-hashing launcher with only `-ExpectedLauncherSha256`. After exit 48
self-hash validation, it checks the fixed `$PSHOME\powershell.exe` and fixed v6
wrapper paths, invokes the wrapper once with seven literal frozen SHA tokens,
passes through the allowed child codes, maps path/launch failures to 49, and
maps null or unknown child status to 50.

## Failure conditions

- Launcher source contains command-file reads, line extraction/indexing,
  pipelines, eval, dot-source, or environment-variable access.
- Native wrapper invocation is not exactly once or argv differs by any token.
- Exit 48/49/50 or allowed-child passthrough is not exact.
- v6 wrapper/parser/collector/loader behavior differs from v5 beyond mechanical
  experiment, run, version, path, and frozen-binding substitution.
- Any real env content/size/hash read, network request, or formal execution
  occurs during Phase2 preparation.

Successful offline implementation remains `NOT_RUN` pending independent
Phase2 review. Even a later successful formal run is capped at
`NEEDS_MORE_DATA`; it cannot establish historical eligibility.

