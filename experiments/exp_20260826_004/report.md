# exp_20260826_004 formal experiment report

## Outcome

The exact frozen formal command was authorized by independent Researcher and
Auditor GO decisions and executed once. It failed closed on the third built-in
attempt for detail `54cf9ec60fc244e3af12beb950e764bd`, after three HTTP 429
responses. The observed process exit was 1, stdout was empty, and stderr was
`ERROR: details/54cf9ec60fc244e3af12beb950e764bd failed closed: HTTP_429`.

The run id is consumed. Partial raw evidence is preserved, but no processed run
or root artifact was committed. The terminal decision is therefore
`INCONCLUSIVE`; `ANNOUNCEMENT_CORPUS_AVAILABLE` was not established.

## Implemented single change

- List `releaseDate` is retained only as `list_release_date_claim_ms` and is the
  explicit claim used for selection and interval counts.
- Detail `publishDate` is retained independently as
  `detail_publish_date_claim_ms`.
- Every selected detail row contains both claims plus exact signed
  `detail_publish_minus_list_release_claim_ms`.
- Both claims require positive non-bool integers. Delta is exact subtraction and
  accepts arbitrary positive, zero or negative magnitude.
- No equality, tolerance, reconciliation, preferred timestamp, ordering
  assumption or expected discrepancy count exists.
- Generic v2 aliases were removed from processed rows, summary and CLI, and are
  explicitly forbidden by schema/loader contracts.
- `time_claim_discrepancies.jsonl` is the exact nonzero-delta subset of sorted
  detail-index rows, in `(catalog_id, article_code)` order. Empty is valid. Its
  path, SHA-256 and realized count are summary-bound and raw-rebuilt by the
  trusted loader.

The module is self-contained stdlib code with no v2 import or runtime reuse.
Opaque code/type-preserving id semantics and all v2 transport, no-auth,
no-proxy/no-redirect, bounded read, retry, global cap, exclusive lease, source
TOCTOU binding, double time bracket, two-full-pass and trusted-loader controls
remain unchanged.

## Exact paired fixtures

The tracked list fixture is 9,949 bytes/SHA
`adbcfcf758ce0c55b772b77de49169702b44759b3e99525e92bb16591d13ce5d`;
the paired detail fixture is 78,567 bytes/SHA
`cb27c5578fa9303516b238b0e4845640b21f397d09e6f5a90fd4446d21a2a8d5`.
They reconstruct the exact claims `1730975333236` and `1730975320602`, and
delta `-12634` ms for list `articles[1]`. Tests use only tracked fixtures.

Because both exact raw bodies end at `}` without LF, two local byte-for-byte
`Copy-Item` operations were the minimal fixture-only exception to apply_patch.
Pre/post lengths and hashes match the preregistered values; the exception is
fully disclosed in `logs/offline_results.txt`. All other writes used
`apply_patch`.

## Frozen implementation verification

- First v3 narrow suite: 32/32 passed in 3.497 seconds.
- Post-hardening narrow suite: 32/32 passed in 3.681 seconds.
- First full repository suite: 190/190 passed in 10.089 seconds.
- Final frozen v3 narrow suite: 32/32 passed in 2.388 seconds.
- Final frozen full repository suite: 190/190 passed in 7.382 seconds.
- Frozen v2 source remains unchanged at
  `30a45ed781e0335838ec7e6643c04c229c8ff53637aec2e2c7a0d928f22adc10`.
- Before formal execution, the formal raw/processed run and all root artifacts
  were absent and the network count was zero.

The sole formal command is bound to the v3 source SHA and retains 866 expected
logical requests plus the absolute 2,598 wire-attempt cap. Its UTF-8 bytes
without newline hash to
`de1a7f3457b1d17a9a2abde043bc9e5240b29fa24d1129d2d2a0c45110dbe4b4`.
The frozen source, tests, pyproject, parameters and hypothesis remained
unchanged through formal execution. The pre-execution command-ledger SHA is
`b9e016e207446f3c25b12c548bd5ff9def8a650e5c0e2d4f7ed89b5262841c98`;
after appending the immutable execution result, it is
`f3427451a0ec220e18a9d7acd5d5311f9dc0bc68206439dbb0d8f2b4926cb671`.

## Formal evidence and failure boundary

The collector made 228 wire attempts across 226 logical request directories:
225 returned 200 and the final three returned 429. The successful responses
were one `time_before`, 54 first-pass list pages, and 170 detail pages. Raw
response bytes total 9,420,210; all 228 body hashes and byte lengths match their
sidecars. The preserved raw tree contains 456 files / 9,705,245 bytes and its
explicit sorted-path tree digest is
`5cf7bae6947864a32773860dfee32295f4c7ab40d4db21c0db5d4f3a63c0fc2b`.
There were no authentication, method, canonical-host, URL-allowlist, or wire
clock-overlap violations in the partial evidence.

The first list pass was complete and reproduced catalog totals 2,234/426 and
the preregistered list-claim interval counts 575/181. It selected 756 details,
of which only 170 were acquired before the 429 failure; 586 remain unacquired.
The second full list pass and `time_after` were never reached. Consequently the
full-pass stability and closing clock-bracket contracts cannot be evaluated.

An offline diagnostic over only the 170 successful detail bodies found 168
zero deltas and two nonzero deltas: -12,634 ms for
`209355888f0042f788899dd1a04a0052` and -6,464 ms for
`541daaa5499d4b34a737c97c50c8547e`. This is explicitly partial and
noncanonical. Since acquisition failed, no
`time_claim_discrepancies.jsonl` artifact exists and these observations are not
a corpus-level result.

The trusted loader was invoked offline and correctly rejected the absent root
summary with `CorpusIntegrityError`. This is the expected fail-closed outcome,
not a loader defect. Complete execution and inspection transcripts are frozen
under `logs/formal_run.txt` and `logs/formal_inspection.json`.

## Temporal and research boundary

Both endpoint timestamps remain source claims, not effective event or listing
times. Even a future successful discrepancy artifact would only measure
disagreement; it would not decide which claim is correct. This incomplete run
cannot recover deleted articles, historical versions, historical
status/permission, listing intervals or eligibility. No event/pair parsing,
Alpha, IC, ML or backtest was performed, so the PIT eligibility gate remains
closed.

## Referenced Skills

| Skill | Source | Purpose | Local path |
|---|---|---|---|
| quant-strategy-research | workspace skill | Preregistration, evidence freeze and research gates | `.codex/skills/quant-strategy-research/SKILL.md` |

The skill-linked `methodology.md`, `experiment-contract.md` and `source-map.md`
files were absent and are disclosed in `manifest.json`; none is claimed as read
or applied.
