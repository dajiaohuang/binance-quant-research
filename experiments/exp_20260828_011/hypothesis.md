# exp_20260828_011 preregistration

## Observation

exp_20260828_010 formal attempt001 stopped on a DNS resolution failure after
durably writing four complete raw/receipt/date-manifest leaves for 2024-07-02
through 2024-07-05. Frozen v4 only resumes immutable whole-month shards and
would therefore request those four dates again.

## Single hypothesis and change

A narrow recovery successor can strictly validate the four complete attempt001
day leaves, bind them into an append-only pointer registry without copying raw
rows, and construct attempt002 so its first request is 2024-07-08.

The adoption validator must bind each leaf's exact raw body, safe receipt,
requested date, page chain, exact Free18 schema, body byte count and SHA-256,
nonempty unique ordered Date+Code identities, and date manifest. It must reject
any missing, partial, inconsistent, redirected, non-200, wrong-date, wrong-page,
wrong-schema, wrong-size or wrong-hash leaf. Only the contiguous complete prefix
of the first month may be adopted.

No raw bytes are copied. The new registry contains source-relative pointers,
sizes, hashes and semantic counts only. A new batch/control/month attempt ID is
required. All other frozen v4 collection, pacing, raw-first, pagination,
stop-first-failure, immutable month publishing and catalog behavior is retained.

## Success and failure

Offline success requires exactly four adopted dates and a dry recovery plan
whose first network date is 2024-07-08 with no overlap between adopted and
network dates. Any other result stops before key access or network.

After the focused tests and freeze, the exact recovery command may run once
without a separate auditor. It has zero automatic retries. A network or data
failure preserves staging and ends the run.

## Claim ceiling

Before a completed formal acquisition the maximum claim is
`JQUANTS_V2_FREE_PARTIAL_DAY_ADOPTION_RECOVERY_FROZEN / NEEDS_DATA`.
Historical eligibility/PIT universe, training, inference, IC, P&L and backtest
claims remain unauthorized.

