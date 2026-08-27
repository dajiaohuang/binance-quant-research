# Experiment evidence index

Experiment directories are immutable research evidence. Failed, superseded and inconclusive
runs remain versioned; a later successor never rewrites an earlier result. Start with each
directory's `report.md`, then inspect `manifest.json`, `commands.txt`, `metrics.json` and the
bound logs or artifacts.

## Current milestones

| Experiment | Result | What it establishes |
|---|---|---|
| `exp_20260714_002` | `REJECTED` | Minimum-hold SMA does not overcome the negative training edge. |
| `exp_20260825_001` | `INCONCLUSIVE` | 24h higher-timeframe gate has too few trades and negative training margin. |
| `exp_20260825_003` | `NEEDS_MORE_DATA` | Unicode-preserving Binance Spot archive inventory. |
| `exp_20260825_004` | `INCONCLUSIVE` | Exact payload acquisition succeeded, but the original close-time gate failed. |
| `exp_20260825_005` | `NEEDS_MORE_DATA` | No-fill `ARCHIVE_KLINE_AVAILABLE` panel; not a trading universe. |
| `exp_20260825_007` | `NEEDS_MORE_DATA` | Synthetic hierarchical Alpha kernel contract. |
| `exp_20260826_001` | `NEEDS_MORE_DATA` | Current/forward PIT snapshot from its 2026 known-at only. |
| `exp_20260826_005` | `NEEDS_MORE_DATA` | Source-bound current-visible announcement corpus. |
| `exp_20260826_007` | `NEEDS_MORE_DATA` | Announcement schedule-claim scan; still not historical eligibility. |
| `exp_20260827_004` | `NEEDS_MORE_DATA` | Synthetic bound historical-evidence adapter contract. |
| `exp_20260827_005` | `NEEDS_MORE_DATA` | End-to-end exact multi-horizon identity contract. |

The repository currently has no Champion and nothing is approved for paper trading. Archive
availability, current exchange status and announcement visibility must not be interpreted as
point-in-time historical eligibility.
