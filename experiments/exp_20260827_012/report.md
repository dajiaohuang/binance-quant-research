# exp_20260827_012

Pre-registered synthetic-only clean-room TIPS V1 contract. No formal command, full repository suite, network request, real-data read, upstream-code execution, HF payload download, or pickle load is authorized in Phase1.

The reverse self-safe attention diagonal and fixed-periodic sinusoidal pairwise bias are `LOCAL_DISCLOSED_CHOICE`, not paper-exact claims. Rolling z-score uses population standard deviation and maps a constant window to zero; this is also a disclosed local choice.

Development result: targeted tests `23/23 PASS`; CPU and CUDA synthetic smokes PASS. CUDA peak allocation was `74,123,264` bytes and on-device repeated evaluation was bitwise deterministic. The smoke override ran one update per teacher, two student updates, and two SWA snapshots; it is not the paper training run.

All earlier development failures are retained in `logs/development_history.txt`, including the initial Windows glob compile invocation, two failing targeted iterations, and the first GPU checkpoint comparison failure. No full repository suite was run.

No-license/data boundary rechecked: upstream source was not read, copied, imported, or executed; the HF pickle payload was not downloaded or loaded; network, real-data, and formal counts remain zero.

Status: `PREFORMAL_AWAITING_PHASE2 / NEEDS_MORE_DATA`. This only supports a candidate `SYNTHETIC_TIPS_CONTRACT_VERIFIED` conclusion after an independently authorized formal run; it is not Alpha, IC, P&L, or empirical evidence.

Phase2 candidate: implementation tree `979bc530b951f2537c3d2a75f814507c00989fa0a9ab1039516b41baa189e9a1`; frozen-hash index `1750360a041d396428dafc04557eb968796159784251d81fc491da2066e897b2`; exact formal-command UTF-8/no-newline SHA `3fbfaee4f014a65697be79bbb5aca2ee2bfa8346da6b93a98d55b36aff9ae4a9`. The command remains unexecuted.

That original candidate received Fresh Phase2 FINAL NO-GO on four integrity gaps. The old freeze index and 23/23 evidence remain as rejected evidence; details are in `logs/fresh_phase2_nogo_and_repair.txt`.

Repair development now proves explicit feature/label row-content identity, mandatory calendar replay of the q=5 session/time path at the teacher boundary, distinct real student trajectories during SWA_ACTIVE with exact tensor averaging, and checkpoint publication only from a pipeline-issued `FrozenSWAStudent`. Fresh targeted tests are 28/28 PASS; fresh CPU/GPU smokes PASS; GPU peak remains 74,123,264 bytes. Formal and full-suite counts remain zero pending Fresh Phase2 review.

Fresh repair candidate: implementation tree `93697386a8b9f9f839afb248f65fe0c169eb6b384632fbfdf87340ce8a766e38`; frozen index `fe8b9ffa785be660a13ba476a7a678fb8fc997e4768761f53c1ab1892c7c70fc`; formal-command SHA remains `3fbfaee4f014a65697be79bbb5aca2ee2bfa8346da6b93a98d55b36aff9ae4a9`. Status is `PREFORMAL_AWAITING_FRESH_PHASE2 / NEEDS_MORE_DATA`.

That first repair candidate received a second Fresh Phase2 FINAL NO-GO because its importable `_PIPELINE_TOKEN` and caller-presented trajectory metadata did not prove that the final SWA model was derived from the claimed states. The rejected freeze and its 28/28 development evidence remain unchanged; the exact finding and repair are preserved in `logs/fresh_phase2_nogo_2_and_repair.txt`.

Repair 2 removes the token authority entirely. `FrozenSWAStudent` now contains every actual SWA update snapshot and update identity; update count, IDs, state hashes, trajectory proof, and the final model are all replayed from those tensors. The frozen average is computed per tensor on CPU in float64, divided once, cast once to the final dtype, and required to equal the final student bitwise. The checkpoint carries the final state and every snapshot under an exact tensor manifest and still requires external manifest-ID and weights-SHA bindings.

Fresh-2 targeted development tests are `30/30 PASS`; independent CPU and CUDA synthetic smokes PASS, including checkpoint roundtrip with all snapshots and two bitwise-identical evaluations while the model remains on CUDA. CUDA peak allocation was `74,123,264` bytes. The full repository suite was not run. Formal, network, real-data, upstream-code, and pickle counts remain zero.

Fresh-2 candidate: implementation tree `36ea6867be14bb579212d6c482405a2175799639d23d5a061cc44049511e7d89`; frozen index `d7f6c41d350d0bcd260b51ad9080e6174c3e3400af15416beafab11075cdd180` (`3753` bytes); exact formal-command UTF-8/no-newline SHA `3fbfaee4f014a65697be79bbb5aca2ee2bfa8346da6b93a98d55b36aff9ae4a9`. Status is `PREFORMAL_AWAITING_FRESH_PHASE2_2 / NEEDS_MORE_DATA`.

Fresh-2 then received a third Fresh Phase2 FINAL NO-GO. Although it stored actual snapshots, `save_checkpoint` still accepted a caller-created `FrozenSWAStudent`; student updates did not issue monotonic pipeline receipts; paper and synthetic update counts were not distinguished; and snapshot construction silently cloned/normalized inputs. The old index and 30/30 evidence remain unchanged. The complete finding, first Fresh-3 development failure, and repairs are preserved in `logs/fresh_phase2_nogo_3_and_repair.txt`.

Fresh-3 makes checkpoint publication pipeline-state-bound. `save_checkpoint` accepts only an exact `TIPSPipeline` in `STUDENT_FROZEN`, extracts its internal student, seven teacher hashes, configuration, step receipts, and snapshots, and rejects model/Frozen/pipeline-like inputs before directory creation. Every successful student step emits a monotonically indexed receipt; each snapshot uses that receipt ID and must bind its exact state. The strict snapshot validator is replayed at construction, Frozen validation, pipeline freeze, save, and load without sorting, cloning, or otherwise normalizing caller input. The paper configuration requires ten ordered SWA updates as a local mapping from its final ten epochs; the typed synthetic smoke requires exactly two.

Fresh-3 targeted attempt 1 failed at 32 PASS/1 FAIL/2 ERROR and is retained. After three local corrections, the final targeted development gate is `35/35 PASS`, py_compile PASS, and CPU/CUDA smokes PASS. Both smokes use the exact pipeline save boundary and snapshot checkpoint roundtrip; CUDA inference is bitwise deterministic on-device and peak allocation is `74,123,264` bytes. Full repository tests are `NOT_RUN`; formal, network, real-data, upstream-code and pickle counts remain zero.

Fresh-3 candidate: implementation tree `11b64659ff1a0f6b27e4b87608c663cbf672ab8294138e0b7e32861db0b151c9`; frozen index `c2d07c8a71244ec8d71972d005d7e307297db31e24980496743935b7173ec21c` (`3884` bytes); formal-command SHA remains `3fbfaee4f014a65697be79bbb5aca2ee2bfa8346da6b93a98d55b36aff9ae4a9`. Status is `PREFORMAL_AWAITING_FRESH_PHASE2_3 / NEEDS_MORE_DATA`. This proves only a pipeline-state-bound synthetic contract; ordinary Python in-memory objects are not claimed to be cryptographically unforgeable.

Fresh Phase2 subsequently gave FINAL GO. The exact frozen command ran once with no retry: exit `0`, `35/35 PASS`, zero failures/errors/skips, and the CUDA test executed rather than skipped. The process wall reported by the executor was `4.27583` seconds. No full repository suite was run.

The formal stdout record is empty (`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`); the complete unittest stderr record is `5526` bytes with SHA `9de8dee4aea6c1c97b0aa63883a9254fcd4c3274c987c65cb534d780ca49a564`. The contemporaneous execution ledger SHA is `2226f0fd02c7cb7c3490de9fba10203306843291b4207812b145dd08c652c4c4`. Postflight revalidated all 20 frozen entries, the implementation tree, and the freeze index with no drift.

Immediately after formal execution, the consumed run entered the then-current state `PENDING_INDEPENDENT_POSTFLIGHT_AUDIT / NEEDS_MORE_DATA`. Formal count was one and retry count zero. Network, real-data, upstream-code and pickle counts remained zero; empirical authorization and historical eligibility remained closed. The formal record index is `de090dfe35e71b7e68b09e772bfd9f3a7c09cfa2fb19f526be69f475b8a1fac2` (`897` bytes).

## Independent audit closure

Independent postflight review returned `FINAL PASS`. The append-only closure is `artifacts/independent_audit_closure.json` (1287 bytes, SHA-256 `5d540427cbc5e307f7be45bc396759ac4860294115384ffcdd54215cb9fd9c5a`). It was created after formal execution and does not alter the formal ledger, result, record index, 20 frozen rows, implementation tree, tests, or the three retained Phase2 NO-GO candidates. Status is now `POSTFLIGHT_INDEPENDENT_AUDIT_PASSED`; the artifact and terminal ceilings are `SYNTHETIC_TIPS_PIPELINE_STATE_BOUND_CONTRACT_FORMAL_VERIFIED / NEEDS_MORE_DATA`.
