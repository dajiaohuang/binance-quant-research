# exp_20260828_010 report

The independent v4 monthly executor is implemented and its 21-file offline
candidate is frozen under SHA-256
`0c9d47e2e6678ac1125b37a11fd3a9e77e2c4b0328bef3062b435a5d9e6b103a`.
Monthly API execution remains gated on a Fresh Phase2 audit
despite the user's general authorization to obtain more data.

The source preflight independently revalidates the exact exp009 final,
acquisition manifest, raw-tree, registry and session list. The v3 trusted loader
thereby revalidates the exp005 Q04 raw/sidecar and exp006 closure as well. Its
source-binding SHA-256 is
`9de9bbd7d6f9acc0c2456816db4ecc1953abeaa7ac01998e96d790c85cc5208d`.
The resulting immutable plan is 23 chronological months, 465 official sessions,
exactly three reuse dates and exactly 462 network dates.

Each month has a unique O_EXCL attempt reservation, raw-first response and safe
receipt persistence, prior-key-only pagination capped at eight pages/date,
strict Free18/date/Date+Code/null validation, full 15-second first cooldown and
inter-request monotonic evidence, no redirect/retry, stop-first-failure, and an
immutable no-clobber final shard. A later batch ID can validate the completed
chronological prefix and continue at the oldest incomplete month. The global
catalog is deterministic, requires all 23 validated immutable shards and is
never overwritten.

Final focused evidence is 22 tests run: 21 PASS and one Windows symlink-creation
privilege skip. Direct v3/v2/v1 regressions are 77 run: 76 PASS and the same
class of Windows symlink skip. Python compilation, PowerShell syntax parsing and
read-only dry planning pass. The full repository suite was not run because this
phase is intentionally limited to the new package and its direct dependencies.

No key was read, no API request was made, no formal execution occurred and no
monthly shard or catalog was produced. At the observed one-page daily payload
size, expected raw payload is about 565 MB (539 MiB). The hard eight-page ceiling
would be about 4.22 GiB. The minimum pacing wait for 462 one-page requests is
6,930 seconds (115.5 minutes), before response and validation overhead.

The maximum claim remains
`JQUANTS_V2_FREE_MONTHLY_EXECUTOR_FROZEN / NEEDS_NETWORK_AUDIT`. Historical
listing eligibility/PIT universe, training, inference, IC, P&L and backtesting
remain unauthorized.

The frozen launcher SHA-256 is
`ff443b57fa185a4a207ac991fefa52e7940eec9188d17fa28bca441d13da9a68`.
The intended one-shot formal command is recorded verbatim in `commands.txt`; its
393 UTF-8 bytes without a trailing newline have SHA-256
`73a489ccb969470fbc1b4ce6510eb59fa146fd541ea7d9e48e0f16181d30bb7b`.

## Formal execution 001

The exact frozen command was executed once and was not retried. It stopped with
exit code 40 on send attempt 5 because local DNS resolution raised
`socket.gaierror: [Errno 11002] getaddrinfo failed`.

The raw-first staging tree safely retains four HTTP 200 pages for 2024-07-02
through 2024-07-05: 17,492 rows and 4,850,787 raw bytes. Their partial raw-tree
SHA-256 is
`2e75b8dae0f7e13b7b65e18111143279075df4d231120854f813e864a08a5493`.
The fifth send attempt failed before any HTTP response or body was available,
so it has no raw/receipt pair. Completed receipt send gaps are each exactly
15,000,000,000 ns.

No month reached immutable final state and no global catalog was created. The
incomplete attempt remains in its append-only staging directory; 4 of 462
planned network dates were acquired and 458 remain. The key is absent from the
parent process environment after exit, and the entire v4 raw path remains
Git-ignored.

The truthful terminal state is
`INCONCLUSIVE / DNS_RESOLUTION_FAILURE_AFTER_PARTIAL_RAW_FIRST_STAGING`.
