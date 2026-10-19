ToviTunes Production System V2 — Locked Roadmap v1.0
Status: Scope locked by owner instruction, 19 October 2026. This roadmap is a development plan, not authorization to run providers, modify the production environment, merge branches, or publish media. The owner alone may approve a change of scope, deliverables, sequence, technology, or acceptance criteria. A failure does not automatically authorize another task.
1. Mission and boundary
Build a repeatable, locally operated preschool-video production system that creates coherent, educational, enjoyable videos with minimal manual intervention. Improve system-wide behavior across many future episodes rather than hand-fixing individual episodes. Keep existing GLM/NVIDIA creative generation, ACE-Step music, Qwen Image 2.1/ComfyUI artwork, approved Tovi character assets, deterministic rendering and Studio. Preserve provenance, idempotency, recovery, rights and publication safeguards.
Definition of V2 success: Across a predeclared, varied acceptance set, the system completes end-to-end local Generate + Render jobs with technically valid media, consistent art, readable teaching content, intelligible age-appropriate audio and no per-episode manual repair. Publishing requires its own human review and is never part of benchmark execution.
Not in scope (no silent exceptions): new cloud/image/voice models; Azure; additional model providers; a new UI framework; a replacement rendering engine; a new storyboard/asset system solely for convenience; changing the approved Tovi pack; mandatory Whisper/ASR or lip-sync; automatic YouTube publication; mass database rewrites or fresh schema designs unrelated to an approved deliverable; generic codebase rewrites; speculative performance optimizations; feature additions invented by Codex; restarting an episode to hide a failure; wholesale test-suite expansion to increase counts.
2. Verified starting point
Repository: egegoktugbozkir35/ToviTunes.
Current open, draft and unmerged stack, verified 19 October 2026:
PR	Function	Head	Relationship
#44	Working production foundation	f78dcdf1ebb19865552748023b8c9a91bf4829ba	Targets main
#45	Opt-in Visual Quality r3	e68f696d03e68263dbe60a04c4f67d1031f3d408	Targets #44; content included in #47
#46	Studio reliability	9592ba1ee686f8a6a0ea5ac79684f5875291633c	Targets #44; content included in #47
#47	Integration and retired-model fix	2ff081c03668aaba2d434546f97febd084dfb6c9	Targets #44; includes #45 and #46 work
#48	Qwen art quality and deterministic cue	6b2898a34be85555c996c08a690d47bfd833767b	Targets #47
#49	Evidenced native ComfyUI crash recovery	790da5502a966675b94a32838e303a97fb16503e	Targets #48


Exact-head #49 hosted CI passed (1,187 tests) before a subsequent single real ComfyUI successor image request succeeded on the user's machine. That request left an unselected retained source PNG; it does not constitute an accepted full production render. Preserve original ambiguous ComfyUI task, original provider ledger, selected WAV, and production data. Do not treat the single episode's pending image normalization as a separate milestone in this roadmap; safely retain it as evidence, not a reason to return to one-video repair.
Use the #49 head as the starting integration code base, with clear audit of ancestry. Do not merge individual stacked PRs twice or assume the main branch already contains them.
3. Fixed work packages (six gates)
Gate 0 — Baseline, ownership and benchmark contract
Work: Create an isolated V2 integration worktree from the exact #49 head. Verify branch ancestry, CI, current configs, dependencies, approved character pack, schemas, historical manifests, request ledgers, and all protected artifact hashes. Inventory existing overlapping systems and identify what is already working; do not rewrite it. Freeze the test categories, quality rubric and acceptable provider-request budgets before changing generation behavior. Set file ownership and branch targets for subsequent tasks.
Deliverables: reproducible baseline; branch/PR map; verified protected-artifact ledger; fixed benchmark set and rubric; branch/file-ownership plan; minimal risk register limited to this roadmap.
Exit: baseline green; ownership documented; benchmark agreed; no live provider calls or production mutations.
Gate 1 — GPU lifecycle and durable execution reliability
Work: Make local memory use predictable on the existing approximately 8 GB GPU. Coordinate service ownership and stage transitions so ACE-Step is not needlessly resident when ComfyUI renders; retain a safe music artifact before releasing resources. Preserve ComfyUI exclusive use when required. Keep supported low-VRAM/offload configuration, non-destructive preflight, bounded memory observations, truthful UI readiness and robust startup/stop behavior. Generalize existing durable crash and ambiguous-request safeguards without automatically resending uncertain tasks. Make successful-source ingestion and normalization resume safely after an interrupted process; no special-case per episode.
Owned area: local service supervisor, orchestration resource boundaries, recovery contracts and focused tests. Coordinate any necessary edit in shared rendering/asset files at integration, not concurrently with art work.
Deliverables: documented service lifecycle, repeatable local launch profile, state/ownership tests, provider-free interrupted-stage reuse tests, memory evidence from limited user-authorized image probes.
Exit: music stays reusable across stage transitions; no double submissions; service handoff works without manual GPU cleanup during admitted test scenarios; the representative existing Qwen workflow can execute with the conservative profile without OOM in the approved test set. If this is infeasible on current hardware, stop at this gate and present the limitation—do not add a model/provider as a workaround.
Gate 2 — General-purpose image and asset quality
Work: Consolidate coherent episode-world prompts, shared art direction, subject-scoped Qwen illustration prompts, versioned safe alpha extraction, explicit deterministic geometric cues, existing provider receipts, and scene-to-asset binding. Ensure assets are physically coherent and readable at phone size; detect/stop visibly unusable or technically unsafe artwork without silently repainting or admitting it. Keep AI freedom for real illustrated subjects and environments. Do not add an AI-image judge. Do not build topic-specific rules, arbitrary free-text cue classification, or a new asset system.
Owned area: environment and episode asset prompting, normalization/admission, explicit cue handling, related source tests. Creative-plan/schema changes must be coordinated with Gate 3, not concurrently edited by multiple agents.
Deliverables: small cross-topic raw/processed/composited comparison set; environment and foreground contracts; rejection diagnostics; frozen identity/version checks; proof that historical admitted sources are reused.
Exit: representative indoor and outdoor sources are coherent; alpha processing retains pale details or refuses unsuitable artwork; teaching objects are recognizable at 360×640; clear source/renderer attribution for failures. Use only owner-authorized small Qwen A/B requests. No full episode run at this gate.
Gate 3 — Creative, educational and musical direction
Work: Improve the existing creative prompts and constraints for age-appropriate concepts, clear teaching actions, varied scene progression and feasible Tovi performance. Reduce repeated static visual arrangements and unsupported character actions through better planning rather than adding new animation capabilities. Improve lyric simplicity, singability, intelligibility and vocal direction using the existing ACE-Step workflow. Keep creative freedom, novelty and standard model fallback. Technical rules should reject unsupported or unsafe outputs, not force every song or scene into a rigid template.
Owned area: creative prompt/policy layers and music-spec direction; coordinate compilation into visual assets with Gate 2's frozen contracts. No new voice model, new ASR dependency, re-architected planner, new automatic creative evaluator, or manual topic writing to manipulate scores.
Deliverables: versioned prompt changes and rationale; offline feasibility tests; bounded authorized song/creative samples; human lyric and pronunciation listening rubric; evidence of different viable scene types.
Exit: target vocabulary and teaching actions remain clear; lyrics are singable, age-appropriate and understandable in human review; creative scenes are renderable by the approved character pack; no extra automatic provider runs.
Gate 4 — Compositor, motion and Studio production ergonomics
Work: Integrate the strongest existing Visual Quality r3 motion/pose and layout behavior into the combined workflow once cross-topic evidence supports it. Improve only demonstrated general rendering problems, including camera continuity, sprite pose transitions, object scale/grounding, and stable mobile framing. Ensure Studio truthfully shows readiness, retained work, recovery blocks, QA and local-versus-published state. Preserve the human publication gate. No new renderer, character rig or frontend framework.
Owned area: render composition/motion/QA and existing Studio views. Start after Gate 2/3 contracts stabilize; use separate commits and the existing tested rendering path.
Deliverables: cross-topic frame/motion comparisons, producer/studio smoke test, validated old-manifest compatibility, opt-in-to-default decision supported by evidence.
Exit: no critical ghosting/collage introduced by the renderer, approved Tovi stays prominent, educational subjects remain legible, QA and Studio states are correct; legacy artifacts still decode; no auto-publication. Change renderer default only through the acceptance decision at Gate 5, not during development.
Gate 5 — Frozen batch acceptance, integration and V2 release decision
Work: Once Gates 0–4 pass individually, integrate the approved branches in one isolated worktree; run full Windows CI, strict Mypy, Ruff, pack lock and recovery/publication regression checks. Freeze code and rubric. Then, with separate explicit operator approval for live provider effects, run a single predeclared batch of three newly and autonomously generated local episodes spanning three different categories: (1) everyday routine/action, (2) animals/nature, and (3) color/shape/counting. The creative system chooses each actual topic within its category. The batch is a system benchmark; do not author individualized fixes between episodes. Render standard and candidate polished variants from identical sources when applicable. Evaluate complete audio, native-resolution video and raw artwork under the same rubric, without manually replacing assets.
Must-pass criteria for V2 release candidate:
- 3/3 technical: normal end-to-end RENDER outputs with correct audio/video, full decode, admitted sources, valid receipts and no unauthorized resends; no publication.
- 3/3 critical visual: no stacked/mixed-world collage, no severe alpha holes, no unusable core teaching subject, no major sprite ghosting or obvious ungrounded tangible object; foregrounds readable at mobile scale.
- 3/3 core teaching/audio: objective and key vocabulary understandable to a human listener, no materially misleading educational content, sensible scene progression and no severe vocal intelligibility failure. Natural small imperfections are acceptable; incomprehensible vocals are not.
- 3/3 unattended within approved policy: no handcrafted topic, per-scene substitutions, manual sprite edits, or bypasses to achieve acceptance. Normal documented operator control and human review are permitted.
- Operational safety: no mutation of authoritative historic data, no lost identities, clean failure handling, original music/source receipts preserved, and publishing remains explicitly gated.
- Repo health: full hosted Windows CI and lint/type/character checks pass on the combined exact SHA. No existing coverage disabled to pass.
Deliverables: three-run batch report with complete video/audio/source-art evidence, regression summary across the frozen fixtures, code/config/hash preservation audit, release decision and updated operator runbook. Avoid committing generated private media or secrets.
Exit decision: if every must-pass condition is satisfied and the owner approves human editorial quality, classify V2 RELEASE CANDIDATE; only then propose a controlled merge sequence into main with owner approval. If one or more hard gates fail, report the aggregate, the generalizable root cause, and the smallest in-scope correction; stop, do not automatically regenerate, create another task, weaken standards, or add a new phase. A new validation batch, if needed, requires explicit approval.
4. Parallelization and dependency rules
- Gate 0 first, shared and sequential.
- Gate 1 (GPU/recovery) and Gate 2 (art/asset quality) may run in parallel isolated worktrees only with nonoverlapping file ownership. Shared-file edits require one owner and integration coordination, not concurrent modifications.
- Gate 3 starts after Gate 2's prompt/asset contracts are frozen. It may use separate focused Codex agents for lyric and planning review only if their owned files truly do not overlap; otherwise sequential.
- Gate 4 starts after the relevant Gate 2 and Gate 3 interfaces are stable.
- Gate 5 begins only after every previous exit criterion passes. No running multiple Qwen/ACE-Step processes concurrently to accelerate tests on this GPU.
- Each Codex task must specify gate number, permitted files, acceptance checks, forbidden changes, provider-call budget, worktree/branch target and stop condition. Codex may not silently create the next task.
5. Non-negotiable change control
1. Version is fixed: this file is v1.0; treat it as the only authoritative scope. A PR description, Codex suggestion, failed test or assistant recommendation cannot modify it.
2. No additions without owner approval: no new workstreams, models, providers, features, infrastructure, source-data migrations, test categories, acceptance thresholds, or extra live runs. None may be justified with “while we're here.”
3. No substitutions: if a work package is blocked, report the blocker. Do not switch to an easier objective, reset the episode or quietly relax validation.
4. Changes require a written change request: what changes, why it is necessary, how it changes resources/timeline/risk, and which existing approved work it displaces. Implement only after the user expressly approves that exact change; record a new version and a diff. Silence is not approval.
5. Provider and deployment gates: roadmap approval is not live-generation approval. Each proposed provider-call batch and any merge, publication or production-data change requires separate explicit user authorization.
6. No progress theater: passing tests or one polished episode is not release acceptance. Make evidence-based gate decisions with honest PASS / BLOCKED / FAIL reports.
7. No indefinite patching: one evidence-based in-scope fix may be proposed after a failed gate; the user decides whether to pursue it. Do not automatically create additional prompts or phases.
6. Required reporting format for every Codex task
A one-page gate report must state:
- Gate and work package, exact branch/commit, files actually changed.
- Confirmed general root cause and completed system-level correction.
- Before/after evidence from multiple representative categories (where relevant), not only a single episode.
- Provider calls and resource effects, or explicit zero calls.
- Focused checks and exact-head hosted CI status; no invented passes.
- Preserved data, model files, selected media, fingerprints, and publication boundary.
- Remaining blocker and only one of PASS / BLOCKED / FAIL.
- No proposed additional work unless it is already explicitly inside this roadmap; deviations require the owner's written approval.
7. Scope-lock declaration
ToviTunes V2 v1.0 comprises exactly Gates 0–5 as written. There is no automatic “V2.1”, follow-on backlog, extra model evaluation, major repo cleanup, alternate audio stack or fourth acceptance run. Any such item is outside this roadmap until the user explicitly authorizes a versioned amendment.
The existing successful/ambiguous request ledger and unfinished episode are preserved as historical test evidence; they are not the development roadmap's unit of work.
