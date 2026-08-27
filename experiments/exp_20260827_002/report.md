# exp_20260827_002 report

Status: `POSTFORMAL_AWAITING_AUDITOR / INCONCLUSIVE`.

## Formal attempt outcome

Researcher and Auditor authorized the frozen line, and all seven SHA, the
formal-line SHA, env-file metadata, parent environment, and formal-path
preconditions passed. The sole external launch attempt then exited `1` in
`0.585s`; stdout was empty and stderr was nonempty and suppressed rather than
propagated.

The failure occurred in the orchestration command before the wrapper started:
PowerShell returned the unique line as a scalar string, and the launch script
incorrectly indexed `[0]`, selecting only its first character `p`. Therefore
the exact frozen formal line did not start. Wrapper reservation and ledger,
collector control, staging, and final are all absent; the collector was not
invoked and request count is exactly zero. Parent `BINANCE_READ_ONLY_API_KEY`
remained absent. The env file was not read by the wrapper or postflight.

The one-attempt/no-retry authorization is treated as consumed despite the
absence of the repository reservation. This run must not be retried without a
new experiment and fresh authorization. No data result was produced.

This experiment is preregistered to replace only the v4 env-file parser with
`STANDARD_ENV_GRAMMAR_V1`. Collector, loader, transport, schemas, derived
semantics, and the `NEEDS_MORE_DATA` ceiling remain unchanged except for
mechanical v5 identity and paths.

The parser accepts LF and CRLF independently per line, including mixed files;
bare CR remains invalid.

## Offline implementation result

- Targeted tests: fresh mixed-line-ending freeze 43/43 PASS in 17.993s,
  after one preserved initial 42 PASS / 1 FAIL caused solely by an extra copied
  EOF newline in collector/loader.
- Full repository: fresh run 518 PASS / 2 expected lifecycle mismatches / 520
  total in 71.449s.
  The mismatches are the preserved, consumed exp009 and exp001 wrapper
  reservations; neither old test nor evidence was changed or hidden.
- py_compile, corrected PowerShell parse, and strict JSON: PASS.
- The first standalone PowerShell parse command was invalid because its outer
  shell expanded `$ErrorActionPreference`; its diagnostic was retained and a
  no-interpolation parse then passed.
- v5 collector and loader are byte-equal to v4 after only experiment/version/
  path normalization. Five endpoints, request limits, derived rows, loader,
  commit protocol, and semantic ceiling are unchanged.
- v5 formal final/staging/collector-control, reservation, and ledger are absent.
  Network access count is zero and formal execution remains false.
- The real `.env.binance.local` contents, size, and hash were not read.

## Frozen Phase2 candidate

- wrapper: `319b5a0d8e0c7e5ba6380ebf36afe90999ea359f29c042325d3c51fea6276a82`
- collector: `85c6ed716e062d700dd0818ef8ba5a6e256a69e0ac02811f6bc49991c6c7c215`
- loader: `f88c975d29268dc8def22f744c79a01d1b27e15b41364862755173c639cc5d02`
- source contract: `7bea7114c0d62f222217eb376424669539cc890b36e4199e852429e7e6281d2a`
- schema: `ef2df4137f4485a75e986741593e5fa2360faa8f2079133c6d6a13de82b5b951`
- parameters: `df1e045db9b35394bb44cf3548e699107582034ce8ce9292c4d489c152cdf2f4`
- tests: `07396891c7848046fbc8522d47d5b20921405bcdf4bb52c24c5a2c297f2061cd`
- formal line: `3ca044b860b274d83a75c22c0d36a553587d0e58750db33d7d3680eb6b79e366`

No exact formal run, network request, eligibility evaluation, Alpha, IC, ML,
P&L, or backtest occurred. The experiment is `INCONCLUSIVE` pending postflight
audit; the failed outer launch attempt is retained and must not be retried.

## Referenced Skills

- `.codex/skills/quant-strategy-research/SKILL.md` — reproducible experiment
  and evidence-gate workflow.
- The Skill-linked `references/methodology.md` and `experiment-contract.md`
  were not present in this checkout and therefore were not used.
