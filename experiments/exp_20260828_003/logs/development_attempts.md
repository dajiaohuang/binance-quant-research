# Development attempts (not formal)

- Initial query-contract slice: 6/6 PASS.
- After correcting the split expectation from effective date 2025-04-01 to
  ex-right observation date 2025-03-28, the first rerun had 5 PASS / 1 FAIL.
  The failure was a test selector that chose the other Q4 symbol, not a
  production semantic failure. The selector was made symbol-exact; rerun 6/6
  PASS.
- First expanded authority/loader run: 12 PASS / 2 FAIL. One negative mutation
  was accidentally a no-op; one secret scan incorrectly included the copied
  frozen test source containing the synthetic sentinel literal. Both tests were
  repaired without weakening production checks; rerun 14/14 PASS.
- First full launcher-inclusive run: 9 launcher failures. Windows PowerShell
  `-NoProfile` on this host did not expose `Get-FileHash`, so every path stopped
  before key read with exit 41. The launcher now computes whole-file SHA-256
  through .NET `FileStream` and `SHA256`; launcher-only rerun 4/4 PASS.
- Expanded contract run: 24/24 PASS.
- Final candidate targeted run: 27/27 PASS, zero skip.
- First mechanical parse command failed before parsing because PowerShell `[ref]`
  targeted uninitialized variables. The corrected mechanical command initialized
  both variables; Python compile, PowerShell parse and strict experiment JSON
  parse then all PASS.

All attempts used synthetic temp roots/transports. Network request count, retry
count, formal execution count, real-data access and real local-key reads were
all zero.
