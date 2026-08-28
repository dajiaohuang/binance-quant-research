# exp_20260828_005 — J-Quants V2 official-schema raw-first source probe V4

Parent `exp_20260828_004` is immutable. Its single formal attempt failed closed
after Q01 because the preregistered master schema and calendar semantics did not
match the official V2 contract.

## Observation

The official J-Quants V2 Listed Issue Master specifies exactly 14 response
fields: `Date`, `Code`, `CoName`, `CoNameEn`, `S17`, `S17Nm`, `S33`, `S33Nm`,
`ScaleCat`, `Mkt`, `MktNm`, `Mrgn`, `MrgnNm`, and `ProdCat`.
Source: https://jpx-jquants.com/en/spec/eq-master

The official Holiday Division contract defines `0` as non-business day, `1` as
business day, `2` as a TSE half-day trading session, and `3` as a non-business
day with OSE holiday trading. Therefore TSE sessions are `1` and `2`; `0` and
`3` are not TSE sessions.
Source: https://jpx-jquants.com/en/spec/mkt-cal/holiday-division

The current official non-Premium Daily Bars response has exactly 18 base keys:
`Date`, `Code`, `O`, `H`, `L`, `C`, `UL`, `LL`, `Vo`, `Va`, `AdjFactor`,
`AdjO`, `AdjH`, `AdjL`, `AdjC`, `AdjVo`, `MktCap`, and `ExRT`. `UL`/`LL` are
`0`/`1`; `MktCap` is nullable and otherwise finite nonnegative; `ExRT` is null
or `1`/`2`/`3`. Premium-only morning/afternoon keys are absent for Free.
Source: https://jpx-jquants.com/en/spec/eq-bars-daily

The same source states that adjusted prices are rounded to one decimal place.
This source probe therefore validates finiteness, positivity, OHLC ordering and
null-bar coherence, but does not require the four adjusted/raw ratios to be
numerically identical.

The exp004 collector persisted a response only after semantic parsing, so its
Q02 response was not retained when `MASTER_FIELDS` failed.

## Falsifiable hypothesis

An official-schema-correct, raw-first collector can retain every accepted HTTP
response and a safe nonsecret receipt before semantic parsing, then complete the
same fixed five-query Free source probe under the existing exact-once authority
and fail-closed publication contract.

## Single principal change

Repair the source contract only:

- model the exact official master schema and explicit `product_category`;
- require `ProdCat == "011"` for the two fixed KDDI master rows without
  inferring security type from issue-code digits;
- reject the old `SecType`/`SecTypeNm` aliases;
- model the exact 18-key non-Premium daily-bars schema while rejecting
  Premium-only session fields;
- correct `HolDiv` session semantics;
- atomically persist raw response bytes and one safe receipt immediately after
  redirect/status/content-type/size validation and before strict parsing.

All five query dates, endpoints, query parameters, paging limits, request cap,
pacing, exact-once launcher authority, double source hashing, staging rebuild,
full-tree authorization, no-clobber publication and listing-presence UNKNOWN
policy remain fixed from exp004.

## Failure conditions

- a response reaches semantic parsing before both raw body and receipt exist;
- any persisted receipt contains a key, header, URL credential or unsanitized
  query text;
- the official 14-field master row fails or an old alias is accepted;
- a Q02/Q03 master row is accepted with product category other than `011`;
- the official 18-field non-Premium bar fails, a Premium-only session field is
  accepted, or nullable `MktCap`/`ExRT` is mishandled;
- valid one-decimal adjusted prices are rejected by an exact-ratio assertion;
- `HolDiv` 1 or 2 is rejected as a TSE session, or 0/3 is treated as one;
- a disk-write failure permits parsing, semantic claims or publication;
- pagination changes fixed base parameters or lacks prior-key binding;
- frozen source can import or execute before launcher authority;
- a code-filtered master query mints positive listing-presence evidence;
- a second formal invocation can consume or alter the same run identity.

## Phase 1 boundary

Phase 1 permits preregistration, offline implementation, synthetic/regression
tests, static checks and fresh hash freezing. It forbids reading the local key,
network requests, formal execution, training, inference, IC, P&L, backtests and
empirical eligibility conclusions.
