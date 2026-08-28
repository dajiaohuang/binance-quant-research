# Official Financial ML Reproductions

This page is a local evidence index, not an empirical model leaderboard. “Available” means that an upstream identity is known; “acquired” means exact bytes are present locally and bound by a recorded manifest. Those states are not interchangeable. None of the experiments below establishes Alpha, IC, P&L, backtest performance, or readiness for trading.

## Current evidence

| Method | Paper authority | Repository identity and license | Local implementation | Formal evidence and ceiling | 16 GiB GPU evidence | Acquired assets and missing data |
|---|---|---|---|---|---|---|
| Kronos | Kronos public-checkpoint interface; the local exp007/010 source contract does not freeze a paper DOI | `https://github.com/shiyu-coder/Kronos` at `67b630e67f6a18c9e9be918d9b4337c960db1e9a`; repository source is MIT | Vendored audited source in `third_party/kronos/67b630e67f6a18c9e9be918d9b4337c960db1e9a/`; offline wrapper in `src/quant_research/alpha_models/external/kronos_offline_reverification.py` | exp010 independently audited `SUPPLY_CHAIN_AND_OFFLINE_INTERFACE_VERIFIED / NEEDS_MORE_DATA`; 11/11 targeted tests and an offline mini/small interface verification passed | exp007 mini + Tokenizer-2k CUDA interface smoke emitted 24 finite rows with peak allocation `74,509,312` bytes; exp010 separately closes the v2 supply-chain and offline-load boundary | Exactly 19 files / `562,430,552` bytes exist at `data/raw/kronos_official_v2/`: pinned configs, safetensors, four source/license files, and public sample/regression fixtures. Repository source is MIT, but checkpoint-weight redistribution permission has not been independently established. The original paper evaluation corpus was not acquired. |
| SSPT | *Pre-training Time Series Models with Stock Data Customization*, arXiv `2506.16746`, DOI `10.1145/3711896.3737005`, KDD 2025 | `https://github.com/finint/SSPT` at `a2940e4eac7202d2d8c1dfc1e88fa3c811485b8a`; no license found | Clean-room implementation in `src/quant_research/alpha_models/sspt_v2/`; no upstream source was copied, imported, or executed | exp011 independently audited `SYNTHETIC_TYPED_METHOD_CONTRACT_FORMAL_VERIFIED / NEEDS_MORE_DATA`; 28/28 formal tests passed | Synthetic CUDA smoke peak allocation `76,795,392` bytes, below the frozen 2 GiB development cap | Upstream bytes and original datasets acquired: `0`. All five paper datasets remain `NO_GO_NO_LICENSE_OR_PROVENANCE`; no pickle was downloaded or loaded. |
| TIPS | arXiv `2603.16985v2`, DOI `10.1145/3770855.3817749`, KDD 2026 | `https://github.com/AbnerTeng/TIPS` at `799fea2ecd06a9e9035897382471092278021553`; no license found | Clean-room implementation in `src/quant_research/alpha_models/tips_v1/`; no upstream source was read, copied, imported, or executed | exp012 independently audited `SYNTHETIC_TIPS_PIPELINE_STATE_BOUND_CONTRACT_FORMAL_VERIFIED / NEEDS_MORE_DATA`; 35/35 formal tests passed and the CUDA test executed | Synthetic CUDA smoke peak allocation `74,123,264` bytes with bitwise repeated on-device inference | Upstream bytes and original datasets acquired: `0`. HF `Abner0803/Trading-Benchmark-raw` is availability-only (approximately 508 MB) and remains unacquired because it is pickle-based and lacks an established license and provenance. |

DDGL-Net and Causal-DFM were excluded from this closeout because no positive, author-linked official repository chain with an adequate local source contract was established. A community DDGL repository identity is not an official-repository proof. Neither method has a formal implementation claim in this index.

## Reproducible local commands

These commands exercise interface or synthetic-contract evidence only. They do not download data and do not produce empirical returns.

The exp010 Kronos offline-verifier invocation with expected manifest SHA
`3d2b370f172b17f090099d8ef2e3d9e530ed4d6cf4cb3c2ef57d04db7747d52e` and
expected acquisition-manifest SHA
`811cd603d640a7acfab7776e3a97a9a753a0e3d053670587eb700f89167419c6`
is a historical exact-once command whose run has already been consumed. It is
recorded in exp010 evidence and must not be rerun. The safe regression command
below does not publish or repeat that verifier run.

```powershell
# Kronos: safe unit-regression command; does not repeat the consumed verifier run
uv run --extra modern-ml python -m unittest tests.test_kronos_offline_reverification -v

# SSPT: typed synthetic contract and development smokes
uv run --extra modern-ml python -m unittest tests.test_sspt_v2 -v
do { $ssptCpuSmokeOutputPath = Join-Path ([System.IO.Path]::GetTempPath()) ("sspt_v2_cpu_" + [guid]::NewGuid().ToString("N") + ".json") } while (Test-Path -LiteralPath $ssptCpuSmokeOutputPath)
uv run --extra modern-ml python -m quant_research.alpha_models.sspt_v2.smoke --device cpu --output $ssptCpuSmokeOutputPath
do { $ssptCudaSmokeOutputPath = Join-Path ([System.IO.Path]::GetTempPath()) ("sspt_v2_cuda_" + [guid]::NewGuid().ToString("N") + ".json") } while (Test-Path -LiteralPath $ssptCudaSmokeOutputPath)
uv run --extra modern-ml python -m quant_research.alpha_models.sspt_v2.smoke --device cuda:0 --output $ssptCudaSmokeOutputPath

# TIPS: pipeline-state-bound synthetic contract and development smokes
uv run --extra modern-ml python -m unittest tests.test_tips_v1 -v
do { $tipsCpuSmokeOutputPath = Join-Path ([System.IO.Path]::GetTempPath()) ("tips_v1_cpu_" + [guid]::NewGuid().ToString("N") + ".json") } while (Test-Path -LiteralPath $tipsCpuSmokeOutputPath)
uv run --extra modern-ml python -m quant_research.alpha_models.tips_v1.smoke --device cpu --output $tipsCpuSmokeOutputPath
do { $tipsCudaSmokeOutputPath = Join-Path ([System.IO.Path]::GetTempPath()) ("tips_v1_cuda_" + [guid]::NewGuid().ToString("N") + ".json") } while (Test-Path -LiteralPath $tipsCudaSmokeOutputPath)
uv run --extra modern-ml python -m quant_research.alpha_models.tips_v1.smoke --device cuda --output $tipsCudaSmokeOutputPath
```

## Paper configurations versus synthetic overrides

- Kronos interface configuration: mini + Tokenizer-2k, context 256, prediction length 24, sample count 1, batch 1, fixed seed. This is an interface smoke, not a training reproduction or forecast-quality result. Small + Tokenizer-base is used for the eight-row official regression fixture; base is statically bound but not required for the closeout.
- SSPT clean-room configuration: lookback 16, complete MA30 warmup, 25 frozen features, `d_model=128`, four heads, two encoder layers, FFN 512, and a 2 GiB synthetic GPU cap. Development smoke performs one minimal pretrain/fine-tune/inference/checkpoint path; it is not the paper training schedule.
- TIPS paper configuration recorded locally: lookback 20, eight features, `d_model=64`, four heads, two layers, FFN 256, seven teachers, 100 teacher epochs, 20 student epochs, Adam `1e-4`, effective batch 256, and ten final SWA updates. The explicitly synthetic-only override runs one step per teacher, two student steps, and exactly two SWA updates. Local disclosed attention/bias choices are not paper-exact claims.

## Legal next steps

1. Keep Kronos weights local unless their redistribution license is independently established; new inference work must continue to use the pinned v2 raw tree and zero-network verifier.
2. For SSPT or TIPS, obtain a separately licensed, point-in-time dataset and freeze its calendar, universe, corporate-action, split, and provenance contract before any empirical training.
3. Do not download or load the unlicensed SSPT/TIPS pickle payloads. If upstream licensing changes, create a new experiment and source contract rather than backfilling these experiments.
4. Run empirical Alpha, IC, P&L, or backtests only in a new preregistered experiment after data and eligibility gates pass.

## Referenced evidence

- Workspace skill: `.codex/skills/quant-strategy-research/SKILL.md`
- Kronos closeout: `experiments/exp_20260827_010/`
- SSPT closeout: `experiments/exp_20260827_011/`
- TIPS closeout: `experiments/exp_20260827_012/`
