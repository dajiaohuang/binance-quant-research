# Kronos public checkpoint interface V1

Observation: the repository has no frozen, offline-only interface to the public Kronos checkpoints.

Hypothesis: the official MIT source at commit `67b630e67f6a18c9e9be918d9b4337c960db1e9a` and revision-pinned safetensors/config files can be frozen and loaded through a fail-closed, local-only interface on synthetic/public sample inputs.

Primary change: add a public-checkpoint interface and evidence manifests only. No Alpha, IC, P&L, backtest, model selection, or empirical claim is authorized.

Success ceiling: `PUBLIC_CHECKPOINT_INTERFACE_VERIFIED / NO_EMPIRICAL_ALPHA`.

Failure: any revision, allowlist, size, hash, safetensors, schema, compatibility, timestamp, label-isolation, offline, CPU/GPU-smoke, or test gate fails closed.

DDGL exp006 is paused with formal execution count zero; its development artifacts are retained.
