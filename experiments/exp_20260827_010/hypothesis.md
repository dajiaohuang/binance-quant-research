# BOUND KRONOS V2 OFFLINE REVERIFICATION

## Observation

Exp009 acquired 19 fresh v2 files whose bytes, SHA-256 values and authority OIDs match a pre-download expected manifest, but exp009 is permanently `INCONCLUSIVE`: its execution ledger was reconstructed after the fact and its adapter drifted after acquisition.

## Single change

Perform a new zero-network, read-only verification that binds the immutable expected manifest and the retained acquisition manifest by external SHA-256, independently rehashes all 19 raw files and Git/LFS OIDs, verifies the four vendored MIT source files, loads source bytes under a private synthetic module namespace without `.pyc` or `sys.modules['model']`, and checks mini hermetic inference plus the official small regression fixture.

## Failure condition

Any manifest/source/raw/checkpoint/tensor/golden mismatch, network/cache attempt, module collision, or output schema violation fails closed. No file is downloaded, copied, rewritten or repaired.

## Ceiling

At most `SUPPLY_CHAIN_AND_OFFLINE_INTERFACE_VERIFIED / NO_EMPIRICAL_ALPHA`. Checkpoint-weight redistribution permission is not established; the local files must not be redistributed. Historical eligibility, Alpha, IC, P&L and backtesting remain unauthorized.
