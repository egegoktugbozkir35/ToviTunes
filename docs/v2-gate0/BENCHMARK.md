# V2 frozen benchmark contract — candidate v1.0

**PENDING OWNER APPROVAL.** Categories and release requirements below are copied
from [Locked Roadmap v1.0, Gate 5](LOCKED_ROADMAP_V1.0.md). Evidence procedures make
those requirements reviewable; they add no model, judge, mandatory ASR, test category
or implementation gate. Gate 0 runs no benchmark. Owner acceptance of this contract
does not authorize live calls; a separately approved Gate 5 provider batch is required.

## Batch declaration

One predeclared batch, exactly three new autonomous local Generate + Render jobs:

| Batch slot | Fixed category | Topic |
|---|---|---|
| 1 | Everyday routine/action | Chosen autonomously by the creative system at separately authorized Gate 5 |
| 2 | Animals/nature | Chosen autonomously by the creative system at separately authorized Gate 5 |
| 3 | Color/shape/counting | Chosen autonomously by the creative system at separately authorized Gate 5 |

After Gates 0–4 pass, record the exact combined SHA, approved rubric revision/hash,
config and lock hashes, approved provider-call ceilings, service launch profile,
execution order and three slot identities **before the first live effect**. Category
constraints do not license handcrafted topics or lyric/scene edits. Retain each
run/episode identity, including failures. Use the same frozen code, config and rubric
for all three; no per-episode fixes, asset replacement, sprite edits, bypasses or
episode resets. Never replace a failed slot with another run to improve the score.

Normal RENDER is mandatory in every slot and ends at METADATA, before RELEASE and
YOUTUBE (`pipeline/targets.py`). Where applicable, produce standard and candidate
polished variants from identical selected sources in separate preview slots. Record
input identities/hashes and the renderer flags; compare both without manual plate
overrides, substitutions or unrecorded selections. A candidate preview does not
replace a missing/failed normal RENDER output. No Qwen/ACE-Step process concurrency
to accelerate this GPU batch. No live batch, source normalization or render occurs
in Gate 0.

## Must-pass release requirements (roadmap wording)

- 3/3 technical: normal end-to-end RENDER outputs with correct audio/video, full decode, admitted sources, valid receipts and no unauthorized resends; no publication.
- 3/3 critical visual: no stacked/mixed-world collage, no severe alpha holes, no unusable core teaching subject, no major sprite ghosting or obvious ungrounded tangible object; foregrounds readable at mobile scale.
- 3/3 core teaching/audio: objective and key vocabulary understandable to a human listener, no materially misleading educational content, sensible scene progression and no severe vocal intelligibility failure. Natural small imperfections are acceptable; incomprehensible vocals are not.
- 3/3 unattended within approved policy: no handcrafted topic, per-scene substitutions, manual sprite edits, or bypasses to achieve acceptance. Normal documented operator control and human review are permitted.
- Operational safety: no mutation of authoritative historic data, no lost identities, clean failure handling, original music/source receipts preserved, and publishing remains explicitly gated.
- Repo health: full hosted Windows CI and lint/type/character checks pass on the combined exact SHA. No existing coverage disabled to pass.

Each 3/3 requirement is conjunctive. A score average cannot hide a failed episode,
critical visual defect, educational error, severe vocal failure or unauthorized
effect. Missing evidence is unproven and blocks acceptance. Natural small
imperfections may be recorded as advisory only when they do not violate these
conditions. Human owner editorial approval is required even when automated checks pass.

## Consistent criterion-to-evidence rubric

Record each criterion as **meets / fails / unproven**, with timestamped evidence
references and a short reason. These are review observations, not new release
classifications; final task status remains PASS / BLOCKED / FAIL.

| Criterion | What the reviewer judges | Required evidence and attribution |
|---|---|---|
| Technical media validity | Normal RENDER completes through metadata; correct selected audio/video, contiguous coverage and full decode with admitted source graph. | Exact SHA/run/episode/target; final MP4/audio/manifests/QA/metadata IDs and SHA-256; ffprobe report; full FFmpeg decode result; storyboard intervals; selected dependency graph and pack lock. Probe metadata alone is insufficient. |
| Source-art coherence | Each environment is a coherent physical world; no stacked panels or incompatible setting collage; foreground source actually depicts its subject. | Every raw environment/foreground, processed derivative and receipt/profile/workflow/seed hash; side-by-side raw → processed → composited views, including scene starts/midpoints/ends and motion transitions. Identify whether a defect exists in raw pixels, extraction, binding or rendering. No mandatory number of samples is inferred from the preview frames; watch the complete video too. |
| Teaching-object clarity | Core subjects/actions are recognizable, correct and readable at **360×640**, without decorative clutter obscuring them. | Native-size and mobile-size frames/video; teaching objective/key vocabulary and scene/asset references; human notes naming the subject seen. A high-resolution file or large alpha bounding box alone cannot establish clarity. |
| Visual consistency | Character, environments and separately generated subjects belong together in palette/style/perspective; no critical collage/alpha damage. | Cross-scene source contact sheets plus complete native/mobile playback. Record inconsistent anatomy, extra faces, perspective or palette; compare original/raw/processed to locate the origin. |
| Character performance | Approved Tovi remains prominent; scene actions use supported pack poses, with no major ghosting or obvious ungrounded tangible objects. | Pack revision/role IDs, scene composition and motion records, transition playback and adjacent frames. No invented grasp/rig or mandatory lip-sync. Record when a pose communicates the lesson and when unsupported action weakens it. |
| Lyrics / vocal intelligibility | A human listener can understand objective/key vocabulary and vocals; lyrics are singable and age-appropriate, with no severe intelligibility failure. | Full original WAV and final-video audio; exact supplied lyrics/music direction; human listening notes with timecodes and heard/expected words, pronunciation and repetition observations. Listen without reading the transcript first, then compare lyrics. Existing optional ASR may be ancillary evidence, never a new mandatory score/gate or substitute for listening. |
| Educational accuracy | Words, depicted objects and actions teach the intended concept without materially misleading content; progression makes sense. | Selected brief/spec/vocabulary/lyrics and full storyboard/video. Human notes connect timecodes to the claim being taught; disagreements or uncertain factual claims remain unresolved until reviewed by the owner. No new automated evaluator. |
| Recovery / request safety | Existing identities and receipts survive interruption; no unauthorized resend, lost music/source, historic mutation or hidden replacement. | Before/after protected-state and config hashes, request ledger transitions/parent links/native IDs/fingerprints, accepted receipts, operator approvals and ownership events. Include existing offline restart/ambiguous/publication regression results. If a real interruption occurs, document it; do not deliberately inject extra live requests or faults without authorization. |
| Unattended execution | Each slot completes within approved policy without per-episode repair. Normal documented operator service control/review is permitted. | Complete run/progress/blocker logs, every operator action with actor/time/reason, provider accounting and immutable source selections. Distinguish observation/service control from asset/creative edits or bypasses. A recovery requiring manual scene/source substitution fails this criterion. |
| Local versus published state | Local complete/QA/editorial status is truthful and separate from upload/public visibility. Benchmark publishes nothing. | RENDER target boundary, Studio local-state evidence, unchanged publication attempts/visibility records and configuration hashes. No upload or publish call, remote mutation, token refresh or live channel test is required to prove this boundary; use local evidence and offline publication regressions. |

## Numeric contracts and provider budgets

Existing technical contracts are recorded with their provenance, rather than
silently becoming new aesthetic cutoffs:

| Existing constraint | Source / use |
|---|---|
| 1080×1920, 30 fps (fps tolerance 0.01), H.264, yuv420p, AAC; accepted sample rates 32/44.1/48 kHz and positive channel count; portrait aspect tolerance 0.001 | `render/models.py`, `render/qa.py:check_probe`; standard production contract, followed by full audio/video decode. |
| Audio/video/container duration each within `2/30 + 0.005` seconds of admitted duration | `render/qa.py:DURATION_TOLERANCE`; existing two-frame/container-rounding allowance, not a new performance target. |
| Finite decoded PCM, audio duration match within 0.005 s; existing clipping ratio ≤0.001, near-silence ratio ≤0.2, dropout/edge silence ≤2 s | `domain/production_v3.py:AudioClock`, `music/analysis_models.py:AnalysisThresholds`; inherited technical admission. ASR/WER thresholds in historical analysis are **not** mandatory V3 intelligibility thresholds. |
| 360×640 foreground review | Locked roadmap Gate 2 / Gate 5 mobile clarity. No new minimum pixel area, subjective score, LUFS target or voice error-rate cutoff is approved here. |
| Existing ordinary creative repair/fallback policy | `creative/provider.py`, `resilience.py`, `failures.py`: one structural repair per model contract; classified live transient delays (endpoint: 2/5/15 s; server/rate-limit: 2/5 s; read/stream interruption: 2 s); durable retries/fallback IDs retained. These are existing request-safety bounds, **not** an approved batch spending allowance. |

Gate 0 budget is **zero live provider requests**, as explicitly required. Gates
1–4 samples and Gate 5 execution each require their own explicit batch approval.
The roadmap does not approve a numeric batch spending cap. The following candidate
ceilings are derived from existing one-output and plan-bound provider contracts:

| Candidate authorization ceiling | Status and basis |
|---|---|
| One initial ACE-Step song request per episode; three initial song requests for the Gate 5 batch | **PROPOSED — OWNER APPROVAL REQUIRED.** Existing `batch_size=1`, one immutable episode music binding and no regeneration of a successful source. This is a proposed effect budget, not existing authorization. |
| Per episode, at most one initial Qwen request per distinct planned provider illustration plus four planned environment-role requests, less valid admitted/shared reuse; explicit swatches/cues use zero | **PROPOSED — OWNER APPROVAL REQUIRED.** `episode_assets.generate_assets`, `environment_sets.ROLES` and compiled asset keys define the formula. Freeze the actual plan inventory/counts before image effects; unused roles are not quietly removed by Gate 0. No best-of-N samples are implied. |
| No additional image/music attempts, replacement grants or extra candidates charged automatically; failed/uncertain work stops under existing contracts | **PROPOSED — OWNER APPROVAL REQUIRED** as a batch ceiling. Existing no-blind-resend invariants still apply independently. Any authorized recovery must preserve identities and the unattended criterion; budget approval does not permit per-episode manual repair. |
| Creative spending is calculated from the frozen configuration and existing request state machine using the formula below | **PROPOSED — OWNER APPROVAL REQUIRED** for a live batch. Record its contract inventory, configured chain, topic-round bound, retained ledger state and derived reservation before its first provider effect. This accounting does not add retries, repairs, models or topic rounds. |

These proposals neither tighten nor relax the locked release rubric. Unapproved
ceilings authorize **zero** calls. Approving this contract must explicitly accept
or amend its budget proposals; any changed roadmap criterion requires the owner's
versioned written change control. Provider unavailability is a blocker, not permission
to add another model, exceed budget or restart a slot.

### Reproducible creative-provider accounting

Count creative **chat-generation attempts**, including unsuccessful or uncertain
remote-started attempts, not output candidates or tokens. For each structured
contract `s` and visited model `m`, distinguish started initial attempts `i`,
started structural repairs `h`, and started transient-retry attempts `t`:

```text
C(s,m) = i(s,m) + h(s,m) + t(s,m)
0 <= i <= 1; 0 <= h <= 1
C_creative = sum over declared contracts s, visited models m of C(s,m)
```

`h` is available only after invalid structured output from the initial attempt;
failure alone does not grant a repair. There is no second structural repair.
`DurableStructuredGenerator.generate/_response` shares **one retry counter across
the initial and repair attempts** for the same owner/kind-family/input fingerprint.
It does not grant a fresh retry allowance to the repair. Call that allocation
counter `r`: it starts at the ledger's maximum `retry_index`, including already
prepared retries, not at zero on Resume (`CreativeRequestLedger.retry_count`).
For the attempt sum, initial/repair rows have `retry_index=0`; transient-retry
rows have `retry_index>0`. Count each distinct remote-started request once, even
if its eventual status is failed/ambiguous; do not count reused receipts again.

The exact rule for a new transient retry after a live failure `f` is:

```text
allow_retry = (provider == nvidia AND retry_sleep is configured
               AND r < len(classify_creative_failure(f).retry_delays))
if allow_retry: r := r + 1; retry the current initial/repair contract
otherwise: apply the existing failure-scope/fallback rule
```

| Live failure classification | Retry delays / maximum shared counter admitting another retry | Existing dispatch after retry exhaustion |
|---|---|---|
| Endpoint unreachable | 2, 5, 15 seconds; `r < 3` | Go directly to the already configured emergency Ollama transport if enabled; otherwise stop. Do not visit every NVIDIA model for an endpoint failure. |
| HTTP server / rate limited | 2, 5 seconds; `r < 2` | Existing model-scope fallback may advance to the next configured NVIDIA model. |
| Read timeout / stream interrupted | 2 seconds; `r < 1` | Existing model-scope fallback may advance; uncertainty remains recorded under the original request identity. |
| Other classifications | No transient retry | Existing model-scope failures may advance; authentication/configuration and other fail-closed outcomes stop. Invalid structured output follows the single-repair rule above. |

For mixed live failure categories, evaluate the rule against the **current shared
counter** each time. The category allowances are not added together. Its maximum
is therefore `t <= r <= 3` for NVIDIA and `t = r = 0` for Ollama. A supported Retry-After
value in 0–30 seconds changes the wait only, not the request count. Stored failures
or ambiguity do not independently grant live retries after restart. A retained
prepared retry can start once through its existing identity/fence; its already
allocated index is not refunded. A completed response is reused without a new call.

`ResilientStructuredGenerator` follows the configured ordered NVIDIA chain from
its existing/sticky anchor, without wraparound. Each visited model has its own
fingerprinted contract; dispatch requires the existing durable evidence. Success,
fail-closed errors and chain exhaustion stop further attempts. Ollama is visited
only through the existing endpoint-failure path when its configuration flag is
already true; this contract does not enable it.

For reservation before outcomes are known, let `M` be
`1 + len(creative_llm.fallback_models)` and `E` be 1 only when
`fallback_to_ollama_on_endpoint_failure` is already enabled, otherwise 0. The
existing state machine implies this **conservative arithmetic upper envelope**:

```text
B_contract <= M * (1 initial + 1 repair + 3 shared transient retries)
              + E * (1 initial + 1 repair)
           = 5*M + 2*E
B_declared_creative <= sum over declared contracts s of (5*M_s + 2*E_s)
```

This is additive, **not** `(initial + repair) * (1 + retries)`. The envelope does
not override the classification-specific rule or permit unused attempts to be
transferred between models/contracts. For example, with four NVIDIA models and
Ollama disabled, persistent HTTP-server failure permits at most `4*(1+2)=12`
attempts for a contract; persistent read timeouts permit `4*(1+1)=8`; persistent
endpoint failure permits `1+3=4` and then stops. These paths do not gain a repair
or permission to spend the looser `5*M` envelope.

For the **current fresh autonomous V3 RENDER flow**, topic selection has at most
`R = creative_topics.max_generation_rounds` structured pool contracts, followed by
five contracts: episode idea/spec, lyrics, music direction/spec, visual draft and
metadata. The learning brief is compiled locally. A topic pool's configured
`candidate_batch_size` is candidates in one response, not that many provider calls.
Thus, for `N` fresh episode slots with one frozen configuration:

```text
B_fresh_batch <= N * (R + 5) * (5*M + 2*E)
audited example: N=3, R=3, M=4, E=0
               => 8 contracts * 20 = 160 reserved attempts per fresh slot
               => 3 * 160 = 480 reserved attempts across the three slots
```

These deliberately loose totals are **derived accounting bounds, not new provider
limits, spending authorization, a target to exhaust, or additional creative
restrictions**. The exact state-dependent rules always prevail. Existing/sticky
model choice, early success, fewer topic rounds and valid retained artifacts reduce
actual calls; Resume never receives another fresh-run reservation. Record request
IDs, parent/previous-attempt links, prompt/fingerprint, model, `attempt`,
`retry_index`, status and remote-start evidence before/after to reconcile the sum
and preserve already spent allocations. For other separately approved gate samples,
sum only their declared structured contracts; do not assign them a full-episode
reservation. If a future approved implementation changes this flow, derive its
inventory from that frozen code/config under the same owner change control.

Sources: `creative/provider.py`, `creative/failures.py`, `creative/resilience.py`,
`creative/factory.py`, `persistence/requests.py`, `creative/topics.py`,
`creative/service.py`, `services/visual.py`, `creative/metadata.py`, and `config.py`.
Gate 0 policy approval can accept this accounting and the separately marked
ACE-Step/Qwen proposals; it still authorizes **zero live calls**. Every future
gate's live batch requires separate explicit authorization for its declared effects.

## Human review and record

1. Retain a private evidence index per slot with immutable artifact IDs, SHA-256,
   source paths, exact code/config versions, provider accounting and approvals.
   Link evidence without committing generated private media or secrets.
2. A named human reviewer watches the entire native-resolution video, then mobile
   playback, and listens to the complete original/final audio. Review all raw and
   processed art against the same rubric. Record actor, date/time/timezone, rubric
   version, video/audio/source hashes, per-criterion result, precise timecodes,
   critical versus advisory findings, and all operator interventions.
3. Review standard and candidate variants against identical sources. Declare the
   strategy and comparison order; no selected frame or averaged score can overrule
   a complete-playback critical failure. The owner adjudicates unresolved judgments.
4. Record four aggregate counts (technical, critical visual, teaching/audio,
   unattended), each out of three, plus operational-safety and exact-head repo-health
   results. The owner records explicit editorial approval and reasons. Until then,
   release acceptance is blocked even if every machine check is green.
5. Only if all roadmap conditions and owner editorial approval hold, record **V2
   RELEASE CANDIDATE**. Otherwise report the aggregate failure/blocker and the
   generalizable cause; stop. A smallest in-scope correction may be proposed for
   owner decision. No automatic fourth run, replacement batch, new task or weaker
   standard. Any renderer-default decision belongs to that Gate 5 acceptance only.

Suggested record fields (documentation template, no new database schema):

```yaml
batch_id: pending-separate-Gate-5-authorization
code_sha: null
rubric_sha256: null
config_sha256: null
approved_provider_budget_reference: null
slots:
  - category: everyday-routine-action
    run_id: null
    episode_id: null
    evidence_index_sha256: null
    reviewer: null
    reviewed_at_with_timezone: null
    criterion_results: {} # criterion -> meets/fails/unproven + reason + evidence/timecodes
    operator_actions: []
    provider_attempts: [] # request/native IDs, parent, fingerprint, effect type, outcome
aggregate: {} # four required x/3 counts + operational safety + repo health
owner_editorial_approval: null
release_decision: pending
```

The other two slots must use animals/nature and color/shape/counting. This template
does not choose a topic or instantiate an episode.
