# exp_20260828_004 report

Status: `INCONCLUSIVE / CONTROLLED_FORMAL_FAILURE_PARTIAL_STAGING_RETAINED`.

## Outcome

Fresh Phase2 authorized exactly one formal attempt. The frozen launcher consumed
`exp_20260828_004_formal_001` exactly once, made no retries, and exited 20 after
the collector failed closed with `MASTER_FIELDS`. The stage ledger records
self-hash PASS, freeze preflight PASS, env-file read and validation PASS,
collector START/EXIT 20, and final cleanup PASS.

One response was directly persisted before failure:

- query: `Q01_CALENDAR`
- bytes: 166
- rows: 4
- SHA-256: `97f544ad7fecdc2189383b5fdcef925b94914be00e2fd4a59aafb3d8cd63bdee`

The next request, `Q02_MASTER_NORMAL`, is inferred to have received a response
because the collector raises `MASTER_FIELDS` only while parsing that response.
Its body was deliberately not persisted: parsing precedes the atomic response
write. Therefore the run has two requests by controlled-flow inference but only
one directly persisted response. There is no direct evidence of the exact live
Q02 field set, and the report does not reconstruct or claim one.

No authorization artifact or final published run exists. The partial staging
tree and control failure marker were retained for audit. Queries Q03 through Q05
were not attempted.

## Schema diagnosis

The failure is explained by a preregistered schema mistake, not by a financial
result. The frozen loader required `SecType` and `SecTypeNm` and omitted
`ProdCat`. The current official J-Quants V2 Listed Issue Master reference instead
specifies the 14 fields `Date`, `Code`, `CoName`, `CoNameEn`, `S17`, `S17Nm`,
`S33`, `S33Nm`, `ScaleCat`, `Mkt`, `MktNm`, `Mrgn`, `MrgnNm`, and `ProdCat`:
https://jpx-jquants.com/en/spec/eq-master

The calendar fixture also encoded the `HolDiv` polarity incorrectly. The
official Holiday Division reference defines `0` as non-business day and `1` as
business day, with `2` for a TSE half-day and `3` for a non-business day with OSE
holiday trading:
https://jpx-jquants.com/en/spec/mkt-cal/holiday-division

The persisted Q01 response is consistent with that official definition. This is
only a source-contract diagnosis; it does not authorize changing frozen V3 or
using its partial data for model research.

## Postflight

Independent read-only postflight was `PASS`:

- all 10 files still match the external freeze manifest;
- the reservation exists and the nine ledger events exactly match the expected
  controlled execution path;
- the control failure marker is exactly `MASTER_FIELDS`;
- the Q01 object is 166 bytes with the SHA-256 above;
- the control lease exists, authorization is absent, final publication is
  absent, and staging is retained;
- no key was read and no network request was made during postflight.

The existing frozen source, query plan, command binding, reservation, ledger,
control evidence, and partial raw staging were not modified during closure.

## Research boundary

No training, inference, IC, ranking, P&L, backtest, strategy comparison, or
historical eligibility conclusion was produced. `strict_eligible_count` remains
0 and `empirical_authorized` remains false. A corrected contract requires a new
experiment and a new formal run identity; exp004 must not be retried.
