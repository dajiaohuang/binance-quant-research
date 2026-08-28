# exp_20260827_012 — TIPS clean-room V1

## Observation

The repository has no locally verified TIPS implementation contract. The paper identity is arXiv `2603.16985v2`, KDD 2026, DOI `10.1145/3770855.3817749`. The author-linked repository commit is recorded only as unlicensed interface evidence. Its code and payloads are not copied, imported, executed, or downloaded.

## Falsifiable hypothesis

A clean-room, synthetic-only implementation can enforce the frozen same-day cross-sectional feature, clock, seven-teacher, label-isolation, distillation, SWA, inference, and checkpoint contracts without making an empirical Alpha claim.

## Single primary change

Add an independent `tips_v1` typed method contract and synthetic tests/smokes. No market-data, eligibility, Alpha, IC, P&L, or backtest path is opened.

## Failure conditions

- Any future label reaches the student or inference API.
- Cross-sectional loss mixes formation days.
- A teacher is missing, trainable during distillation, or used during inference.
- Any frozen mask/bias matrix, feature vector, q=5 label, state transition, SWA value, checkpoint identity, or external binding fails.
- CUDA is available but the GPU smoke is skipped, non-deterministic on-device, non-finite, or reaches 2 GiB.
- Any upstream repository source, HF payload, pickle, real data, or network request is accessed.

Success remains `NEEDS_MORE_DATA` and only supports `SYNTHETIC_TIPS_CONTRACT_VERIFIED`.

## Fresh-3 authority repair

Checkpoint publication is accepted only from an exact `TIPSPipeline` in `STUDENT_FROZEN`. Each successful student update creates a monotonically indexed receipt; every SWA snapshot binds the latest distinct receipt and is revalidated without normalization. The paper configuration maps its final ten epochs to exactly ten ordered updates as a local disclosed choice, while the typed synthetic-only smoke override requires exactly two. This is a pipeline-state-bound synthetic contract and does not claim that ordinary Python in-memory objects are cryptographically unforgeable.
