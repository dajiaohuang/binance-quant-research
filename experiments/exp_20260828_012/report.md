# exp_20260828_012 report

The narrow operational successor is implemented and remains offline pending
candidate freeze and the single authorized formal003 launch. It discovers and
strictly revalidates 28 complete prior day leaves without copying raw rows:
four from formal001 staging, 17 from the immutable July shard, and seven from
formal002 August staging. The deterministic dry plan has one immutable month
prefix, 434 network dates, first date 2024-08-13, and zero overlap with adopted
dates.

Within each new month attempt, one `HTTPSConnection` configured for the official
API hostname with the platform default verified TLS context is reused and is
closed at month completion or failure. There is no request retry or redirect
fallback.

Focused final tests passed 6/6 once. Python compilation, direct PowerShell parse,
and the read-only dry recovery plan passed. No broad regression or audit was run,
consistent with the user's request to focus on collection.

The frozen formal003 command then ran exactly once and exited 0. It downloaded
434 previously missing session dates in 434 HTTP pages (531,308,897 raw bytes;
1,916,508 rows), with no automatic retry. Combined with 28 validated prior
network leaves and three bootstrap/exp005 reuse leaves, the immutable catalog
represents all 465 official sessions, 2,052,323 rows, and 568,985,417 raw response
bytes from 2024-07 through 2026-05. Missing and unexpected date counts are both
zero. All 23 month shards have immutable validated status.

The catalog SHA-256 is
`d427064ff9669801d27c64cf75b23acfe10230d75a368a8196489e0aa32bfcd0`.
After exit there is no attempt003 staging directory, the key environment variable
is absent, and the raw tree remains Git-ignored.
