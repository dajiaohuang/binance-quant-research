Phase 1 only. No reservation or ledger exists for exp_20260828_005_formal_001.
The exact formal candidate is frozen in ../commands.txt but is not authorized
until a fresh independent Phase2 review returns GO.

Append-only correction: the preceding text describes the Phase 1 state. The
formal run was later authorized and invoked exactly once. This directory now
contains the reservation and stage ledger. The collector exited 20 after all
five source responses had been staged because postflight pacing reconstruction
used receipt wall-clock milliseconds rather than the runtime monotonic clock.
No second invocation occurred. The source final path and authorization artifact
were never created. Terminal: INCONCLUSIVE /
RATE_PACING_POSTFLIGHT_CLOCK_DOMAIN_MISMATCH.
