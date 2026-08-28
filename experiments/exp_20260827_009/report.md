# exp_20260827_009

Pre-registered and fail-closed before download. The available exp007 sanitized manifest lacks the required authority OID field; using its acquired hashes as sole authority would repeat the self-attestation defect this experiment is meant to remediate.

The initial missing-authority blocker was preserved in `logs/predownload_amendment.txt`. Researcher later supplied the full independent 19-row mapping, so implementation and a single fresh v2 acquisition may proceed after source/test freeze. At amendment time no v2 request or raw file existed.

The exp007 statement about an exp006 full run is recorded as stale documentation debt only and is not treated as input evidence. Exp007 runtime manifests and v1 raw payloads are forbidden as exp009 authorities or acquisition inputs.

## Final disposition

`FINAL INCONCLUSIVE / NO-GO`. The formal acquisition command was executed once and downloaded 19 fresh v2 objects (562,430,552 bytes) with zero retry. Its acquisition manifest SHA-256 is `811cd603d640a7acfab7776e3a97a9a753a0e3d053670587eb700f89167419c6`.

The execution ledger was not written contemporaneously. The operator reconstructed it only after the Auditor found that raw evidence existed while manifest counters still said zero. The first postflight also exposed an over-strict adapter rule; the adapter was edited after acquisition. Consequently neither the reconstructed ledger nor the later 11/11 targeted pass can authorize a PASS for exp009. The run is consumed and must never be retried. Raw and acquisition evidence are retained unchanged for the independent zero-network successor.
