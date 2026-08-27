# exp_20260827_001 report

Status: `POSTFLIGHT_PENDING_AUDIT / INCONCLUSIVE`.

## Formal outcome

The single authorized frozen formal command was executed exactly once. It
returned exit code `43` after `0.265804` seconds with empty stdout and stderr.
The run is permanently consumed and must not be retried.

The write-once stage ledger records this exact sequence:

1. `SELF_HASH / PASS`
2. `ENV_FILE_READ / START`
3. `ENV_FILE_READ / PASS`
4. `VALIDATE / FAIL / 43`
5. `FINAL_CLEANUP / PASS`

The env-file bytes were read once by the frozen wrapper, but strict grammar
validation failed before handoff or collector launch. Postflight did not read,
size, or hash the env file. The parent `BINANCE_READ_ONLY_API_KEY` variable is
absent. No collector final, staging, or control path exists, no Binance request
was made, and no data artifact was produced.

Formal evidence:

- command-line SHA-256:
  `4d39ee8ea74183eacdfa3d09ae10aa73aa2b154bbd47061dcf5e81ec9d51c3af`
- reservation SHA-256:
  `38302e346ed61d35a59f011c794ada7fa02cdfe008bf58b59aa4d55b700db24a`
- stage-ledger SHA-256:
  `d110225c057a089351883214c01742b4dc98163ef63c46bdb55bd66ee9a91371`
- actual network request count: `0`
- terminal conclusion: `INCONCLUSIVE`; no data result

The v4 wrapper replaces the superseded, unexecuted exp010 clipboard handoff
with a fixed `.env.binance.local` byte contract. It self-binds, irreversibly
reserves the run, reads at most 4097 bytes through one read-only handle, accepts
at most 4096 bytes under the exact grammar, refuses a preexisting parent key,
and removes only the environment variable it owns. The collector and loader
are value-equal to exp009 after mechanical identity/path normalization.

## Offline verification

- Targeted: 42/42 PASS, including oversize and injected partial-read failures;
  both terminate with exit 42, do not launch the collector, and retain no
  sentinel in ledger or diagnostics. `$buffer` is initialized with the other
  sensitive variables and cleared by the same `finally` block.
- Full repository: 476 PASS and one expected exp009 lifecycle mismatch out of
  477 tests. The mismatch is the preserved exp009 reservation, not an exp001
  regression; it was not modified or hidden.
- Python compilation, PowerShell parse, and strict JSON: PASS.
- Real exp001 final/staging/collector-control paths: absent. The write-once
  wrapper reservation and stage ledger are present; reservation SHA-256 is
  `38302e346ed61d35a59f011c794ada7fa02cdfe008bf58b59aa4d55b700db24a`
  and ledger SHA-256 is
  `d110225c057a089351883214c01742b4dc98163ef63c46bdb55bd66ee9a91371`.
- Parent `BINANCE_READ_ONLY_API_KEY`: absent.
- `.env.binance.local`: present, ignored by `.gitignore:8:.env.*`, and untracked.
  Its contents, size, and hash were not read during implementation or Phase2.

An initial standalone PowerShell parse invocation was malformed by outer-shell
variable interpolation. It produced no experiment output and no network
request; the corrected parse and the in-test parse both passed.

No eligibility evaluation, Alpha research, IC, ML, P&L, or backtest occurred.
The formal run failed before acquisition; the semantic ceiling remains
`NEEDS_MORE_DATA`, but this run itself is `INCONCLUSIVE` and produced no result
artifact.
