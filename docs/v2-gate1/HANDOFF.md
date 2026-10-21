# Sequential ownership and Gate 2 handoff

Accepted Gate 0 base: `ea3495ae44f2f21018e06db19c90ab49f94c70ca`.
Gate 1 branch: `codex/v2-gate1-reliability`.

Exact implementation / shared-file handoff candidate:
`e4431a1a5daac60fe578c73551aa3d0b8a8ec966`.
This is the latest change to `episode_assets.py`, the supervisor and its Studio
regression. It includes the original implementation
`971140a28b3c2ca8eb74d8aa1e42b854640d058b` and ownership/readiness correction
`7659510428f6b9c4a7f77c4506b595fa569de9bf` by ancestry. The earlier candidate is
superseded: the unchanged lost-lease regression required raw evidence registration
before the admission fence; the final correction preserves that behavior. The
Windows bootstrap also uses the base interpreter to avoid an early venv child.

**Release rule:** Gate 2 waits for the final Gate 1 delivery report to release this
candidate after full offline and exact-head hosted Windows checks. The final
report must confirm these remain the latest implementations of the named files.
An intermediate candidate is not authorization for concurrent edits. After release,
Gate 1 stops editing these files. The unresolved live memory-fit criterion does
not grant any provider-call authority to the next owner.

| File held by Gate 1 | Bounded change | Next sequential owner |
|---|---|---|
| `src/tovitunes/render/episode_assets.py` | Returned-source durability, exact source/native receipt reuse, ownership checks and provider-submission boundary only. Existing prompts, cue semantics and normalization algorithms unchanged. | Gate 2, after incorporating the exact implementation commit and verifying recovery/admission checks. |
| `src/tovitunes/orchestrator.py` | Leased GPU admission/stop boundaries and local environment-source continuation. | Gate 2 for required admission wiring only, then the documented later owners. |
| `src/tovitunes/continuation.py` | Read-only recognition of returned/provenance-retained image and environment sources. | Gate 2 for required admission wiring only. |
| `src/tovitunes/services/context.py`, `services/music.py` | Lazy native music-service preparation; successful music reconciles without provider startup. | Gate 2 for required admission wiring only. |
| `tests/test_creative_production_v3.py`, `tests/test_short_production.py` | Additive provider-free source/native-ID/restart/handoff checks; expose the existing mock image provider to lifecycle simulations. | Gate 2 sequentially. |
| `tests/test_studio_v1.py` | Lifecycle, ownership, deferred readiness, busy/pending shutdown and CPU-only Windows process isolation checks. | Gate 4 after this reliability handoff. |

Gate 1's primary modules changed: `web/supervisor.py`, `web/launcher.py`,
`persistence/requests.py`, `render/image_recovery.py`,
`render/environment_recovery.py`. No shared CLI/config/database/error/migration
file required editing. `execution.py` and existing lease/heartbeat behavior are
consumed unchanged and covered by the existing ownership suite.

Gate 2's adapter, workflow, prompts and artwork-processing modules, including
`services/visual.py`, `benchmark/providers.py`, `environment_sets.py`,
`lesson_objects.py`, `artifacts/store.py` and `artifacts/media.py`, were not edited.
No dependency manifests, fixtures, character assets or Gate 0 records changed.

Incorporate the branch through `e4431a1a5daac60fe578c73551aa3d0b8a8ec966` by verified
ancestry, or cherry-pick all three implementation commits above in order, never by uncommitted
file copying. The handoff includes the interacting supervisor/ledger/continuation changes;
do not copy `episode_assets.py` alone. Preserve the successful-source receipt's
native identity and dependencies when applying Gate 2's normalization changes.
Run the added source interruption and repeated recovery tests, native-crash tests,
legacy source/publication tests, Ruff, strict Mypy, character lock and full offline
suite after incorporation. No branch integration or merge is performed here.
