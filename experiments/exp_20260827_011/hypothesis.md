# exp_20260827_011 — SSPT clean-room typed method contract V2

## Observation

The parent `exp_20260827_008` demonstrated a small clean-room SSPT-shaped tensor interface, but its contract did not close several method-fidelity and safety boundaries: complete 30-session feature warmup, typed same-day cross-sections and next-session labels, deterministic MAP masking, stable label registries, strict freeze behavior, or a fully bound dual-file checkpoint.

## Falsifiable hypothesis

An independent V2 implementation can make those boundaries executable and fail-closed on synthetic data while retaining the clean-room model dimensions (`d_model=128`, four heads, two encoder layers, FFN 512). The hypothesis fails if any label reaches inference, any future or missing session is accepted, MAP masking is not reproducible from the frozen SHA-PRF, permutation changes the same-day loss, frozen parameters change bitwise, or a checkpoint can be loaded after provenance/config/tensor tampering.

## Single primary change

Create a typed SSPT method-contract namespace (`quant_research.alpha_models.sspt_v2`) with strict synthetic-only data, training, inference, registry, masking, split, and checkpoint boundaries. This is not a repair or rerun of exp008.

## Forbidden conclusions

No real dataset, upstream payload, pickle, unlicensed upstream source, empirical Alpha, IC, P&L, backtest, validation set, or final test is authorized. Passing only establishes a synthetic method contract and remains `NEEDS_MORE_DATA`.

