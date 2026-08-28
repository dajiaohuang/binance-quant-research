# exp_20260828_012 preregistration

## Observation

Prior runs now contain 28 fully complete network-day leaves: four in formal001
July staging, seventeen in the immutable July final shard, and seven in
formal002 August staging. Formal002 stopped on another transient DNS resolution
failure after completing July.

## Two changes only

1. Deterministically discover and pointer-adopt every fully valid completed day
   leaf from prior failed staging attempts and immutable month finals. Validate
   raw body, receipt, requested date, page chain, exact Free18 schema, byte count,
   SHA-256, unique ordered Date+Code identities and date manifest. Reject any
   ambiguous, duplicated, conflicting, partial or inconsistent leaf. Never copy
   raw rows.
2. Within each new month attempt, reuse one `HTTPSConnection` created for the
   official `api.jquants.com` hostname with the default verified TLS context.
   Close it deterministically at month completion or first failure. Do not retry
   a failed request.

All remaining v4/v5 behavior stays unchanged. Formal003 uses new batch, control
and month-attempt IDs. Offline success requires exactly 28 adopted network dates,
434 remaining network dates, zero overlap and first missing/requested date
2024-08-13. Any mismatch stops before key access or network.

## Claim ceiling

Before full completion the maximum claim is
`JQUANTS_V2_FREE_GENERIC_DAY_ADOPTION_CONNECTION_REUSE_FROZEN / NEEDS_DATA`.
Historical eligibility, PIT universe, training, inference, IC, P&L and backtest
claims remain unauthorized.

