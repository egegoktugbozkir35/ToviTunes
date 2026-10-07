# Ownership and recovery review for PR #43

Review base: `9d9fc5ba70b8947c369345f6399c6246bbd49559`. Main: `327f3a7e49697043cbdf264bf5e3f69461898ec6`. Both remote refs were verified before editing. The donor checkout was verified at `1b82232ed794ccf611cb939ff95a1a52f3b06f52`.

## Concrete fixes

1. Initial Studio Generate lost its run identity when reservation succeeded but the worker raised before returning. Recovery depended on `result`. `Job.recovery_reference` now names production independently of results and survives failed recovery attempts. Diagnostics also use this identity.
2. Run reservation and production intent were separate commits, with no transactional link to Studio. Orchestrator now uses one SQLite transaction for the fresh run, intent and optional job-reference recorder. Recorder errors propagate and roll back reservation before any provider call. Later best-effort job updates preserve the reference committed by that transaction. SQLite begins the write transaction with the run INSERT after ownership renewal; the token is verified read-only before commit to avoid renewing through a competing SQLite writer.
3. `CreativeService.prepare()` could discover the oldest unfinished run or create a run when identity was omitted. It now validates exactly one nonempty identity before progress or provider work. `_run()` performs only an exact lookup. Domain/restart tests reserve explicitly and retain that identity across interruptions.
4. Pre-episode Resume diagnostics received only the unset episode variable. History now records the explicit run ID in the existing diagnostic identity field. History remains best effort and never authorizes continuation.
5. Private upload checked ownership before persisting a successful returned video ID. Lease loss discarded that authoritative evidence. Success is now persisted first, then ownership is checked before returning or allowing promotion.
6. Public promotion needed ownership checks immediately around its durable remote-start boundary, and after retaining a successful visibility response. Those checks now prevent effects by a lost owner while preserving confirmed results.
7. Render acceptance/reuse did not explicitly compare the manifest's storyboard artifact ID or tie the supplied manifest/QA artifacts to the final render's recorded dependencies. Both Orchestrator acceptance and read-only continuation now verify those identities and SHA bindings. Acceptance also verifies manifest/QA owner scope and artifact kind.
8. Environment admission checked ownership once before several asset selections, and illustration normalization/render preparation could separate the last check from selection. Each environment admission and the post-processing selection boundaries now assert ownership again. Retained provider evidence remains reusable.

## Reservation crash boundary

The reference recorder is invoked by Orchestrator inside its reservation transaction. It updates only the currently executing job's existing durable payload on the same SQLite connection/database. It cannot reserve, select a pending run, choose continuation, or invoke a provider. It rejects missing durable jobs, different databases and an already-bound job. No migration was added.

Hard-exit subprocess tests use `os._exit(73)` after run insertion, after intent insertion, after identity update before commit, and after commit before preparation. Before commit, restart finds neither a new run nor intent nor recovery identity. After commit, restart finds all three with exactly the same run ID. A separate HTTP Studio test retains a real root creative receipt, crashes preparation, recreates the app, fails recovery again, then recovers exactly that run without another root request. A SQLite trigger rejecting the identity update proves reservation rollback through the real JobManager persistence boundary.

This closes the application/process interruption window for the Studio path when the production/job store is the same functioning SQLite database. CLI Generate has no Studio job; its run and intent still commit atomically, and explicit run Resume remains available. A dead process's singleton lease must expire before another owner resumes. These guarantees do not cover corrupted/lost storage or a filesystem that violates SQLite durability guarantees. A remote response lost before a durable provider receipt is recorded remains ambiguous and fail-closed.

## Production-path audit

| Area | Inspected production path and retained contract |
|---|---|
| Entry points | Studio Create/Recover/Continue, Web convenience aliases and production/creative CLI aliases delegate to Orchestrator Generate/Resume. No deleted workflow/planner/execution modules were restored. |
| Ownership | One SQLite heartbeat lease in `execution.py`/`persistence/db.py`; stage construction and remote-start callbacks assert ownership. Job/progress/status rows cannot confer ownership. |
| NVIDIA | `creative/factory.py`, `nvidia.py`, `provider.py`, `resilience.py`, `failures.py`, request ledger, topic/director/metadata callers. Chain remains Kimi K3, GLM 5.3, Nemotron 3 Ultra, DeepSeek V4.1 Flash. Configured read timeout remains 1800 seconds; connect timeout alone is bounded separately. Transport retries remain zero. Request UUIDs, remote IDs, immutable terminal evidence and durable bounded retry indices govern recovery. PR #42 boundary tests remain intact. |
| ACE-Step | `services/music.py`, `music/benchmark.py`, `music/ace_step.py`: new production requires local ACE-Step, an immutable creative adapter/binding and endpoint check. Known tasks use retrieval; unknown ambiguous submissions block. Submission's remote-start callback checks ownership after health preflight. Returned audio receipts survive ownership loss before selection. |
| Qwen/ComfyUI | `render/episode_assets.py`, `services/visual.py`, `render/environment_sets.py`, Qwen adapter: normal production still requires Qwen, pins request fingerprints, records remote start, retains returned source bytes/IDs and prevents blind regeneration. Assets and environment selection are fenced separately from evidence retention. |
| Rendering | `services/render.py`, `render/production.py`, `_accept_render()`, artifact store and continuation: configured-root path, nonempty bytes, integrity, episode/manifest/storyboard identity, dependency SHA and exact QA identity precede final admission. No renderer production lock was added. |
| Publication | `publication/service.py`, `youtube/client.py`, release preflight/rights policy and Web aliases: configured channel, private preparation, remote start, durable video ID and same-video promotion remain fail-closed. Uncertain upload/visibility has no automatic new upload. Rights/human review remain independent gates. |
| Continuation | `continuation.py`: read-only SQLite inspection and artifact/receipt evidence, no provider calls or job/progress/history input. Existing poisoned-diagnostic and historical-read-only tests remain. New forged-manifest tests prevent stale/mismatched render reuse. |
| Explicit domain tools | Music/visual benchmarks, lesson-object candidate generation and brand environment intake remain explicit non-production domain tooling. They do not choose episode continuation or own normal production. Their existing review/resource semantics were not rewritten. Production's use of music benchmark storage and episode environment generation receives the Orchestrator ownership callback. |

Historical migrations, Red/Blue saved productions, approvals/rights, reconciliations, artifact IDs/SHA facts and publication IDs were not rewritten. All automated effects run in temporary fixture databases with fake providers or local/mock transports; no live NVIDIA, ACE-Step, ComfyUI or YouTube call is required.

## Manual real-machine acceptance

1. Record the existing failed run/request rows, then run one fresh Studio Generate Draft.
2. Confirm a new creative run and production intent, a dedicated matching job recovery reference, and a new Kimi request UUID. Compare prior failed rows and receipts unchanged.
3. Observe fallback only if actual provider conditions trigger it; do not force extra live requests for validation.
4. If a real pre-episode failure occurs, restart Studio and Recover that exact job/run. Confirm its root request is retained rather than resent; ambiguous evidence should advance only under the existing durable fallback policy.
5. Resume an existing episode and confirm ordinary durable continuation/reuse.
6. Do not publish to YouTube during this acceptance without separate explicit authorization.

Final local validation results, commit identities and the completed CI run are recorded in the delivery report accompanying PR #43.
