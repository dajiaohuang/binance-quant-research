# exp_20260825_007 terminal report

## Decision

`NEEDS_MORE_DATA` (complete). Postflight audit result: `PASS`.

The single preregistered compatibility change was evaluated in the required
order. The first narrow run passed all 35 tests, after which the first full
repository run passed all 85 tests. Their console output was not persisted, so
the same two commands were replayed against the same frozen module and test
SHA-256 values solely to close the raw-evidence gap.

## Evidence replay

| Sequence | Gate | Start (+08:00) | End (+08:00) | Exit | Result | Evidence |
|---:|---|---|---|---:|---|---|
| 1 | Narrow, first execution | Not captured | Not captured | 0 | 35/35, OK | Result recorded; raw stdout absent |
| 2 | Full repository, first execution | Not captured | Not captured | 0 | 85/85, OK | Ran only after gate 1; raw stdout absent |
| 3 | Narrow replay | 2026-08-25 20:15:21.0987098 | 2026-08-25 20:15:21.5131740 | 0 | 35/35, OK; 35 `ok` lines | `logs/gate1_narrow_replay.log` |
| 4 | Full repository replay | 2026-08-25 20:15:21.7866969 | 2026-08-25 20:15:23.8772524 | 0 | 85/85, OK; 85 `ok` lines | `logs/gate2_full_repository_replay.log` |

Replay log SHA-256 values:

- Narrow: `0972f7d6a21c3fdf01a83c907bcb234c5b74b7039296946092caa91a4ca457e3`
- Full repository: `485a169ef68e1cae8b3d5f8466f0a62ac2acf33273a6069bc36dd5d276a8bba7`

The tested module SHA-256 is
`2637ac6b686ef0d6a0a7dc4c07817fd57e2f2724984cdcccac1671528c7044c5`.
The frozen test SHA-256 remains
`6d13ba56248305b9990f9422be79d476b64228d266691f4b6db8ac9416935870`.
The environment used NumPy `2.5.1` and SciPy `1.18.0`.

## Scope boundary

No real market data, network operation, model fit, backtest, CLI, real factor,
score, rank IC, return, turnover or P&L was used or produced. The passing tests
show only that the synthetic fail-closed kernel and repository regression suite
accept the preregistered compatibility repair. They do not establish historical
PIT eligibility or empirical alpha.

## Postflight conclusion

The postflight audit accepted the preregistered single change, frozen hashes,
gate ordering and replay evidence. The narrow suite passed 35/35 and the full
repository suite passed 85/85 in both the first execution record and the raw-
evidence replay. The replay logs and hashes above are the durable evidence.

This successor exists because `exp_20260825_006` terminated `INCONCLUSIVE` on
the SciPy list-input compatibility error. That failed experiment remains closed
and is not overwritten by this repair.

The terminal status is still `NEEDS_MORE_DATA`: only a synthetic research kernel
was validated. Historical point-in-time Binance Spot eligibility is absent, and
no real factor, score, IC, return, turnover, P&L, model fit or backtest has been
performed. Nothing in exp007 qualifies a strategy for paper trading.
