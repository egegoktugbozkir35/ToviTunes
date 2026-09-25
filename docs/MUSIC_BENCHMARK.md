# Colors — Red music benchmark foundation

This is the first-song production track for **ToviTunes Colors — Red**. The accepted audio must exist before the final `TimedStoryboard` is locked. The provider supplies a candidate; ToviTunes owns the approved lyrics, educational truth, audio bytes, timing, provenance, rights, decisions and animation cues. This workflow does not render or publish an episode.

## Canonical inputs

[`colors_red_v1.yaml`](../benchmarks/music/colors_red_v1.yaml) is the versioned brief. It teaches **red is a color**, using only a red apple and red ball as obvious examples. The preferred duration is 30–40 seconds and the initial benchmark ceiling is 45 seconds. The sections are a tiny intro, hook, teaching line, reinforcement and short ending. Style direction calls for a warm, bouncy, simple song with clear downbeats, intelligible English, no frightening or aggressive sounds, and no named-artist imitation. Tovi has no assigned singing voice.

The target is **112 BPM**, with an acceptable requested range of **100–124 BPM**. At 112 BPM a beat is about 0.54 seconds: slow enough for a visible clap or body bob, yet lively enough for a short hook. BPM is a requested control, never trusted as measured audio timing. Actual beat positions are analyzed from retained audio and corrected by a person.

[`colors_red_lyrics_v1.yaml`](../benchmarks/music/colors_red_lyrics_v1.yaml) is an original **pending** baseline. It stores lines, educational claims, rhyme notes and syllable notes separately. Provider translation lives in each persisted request; provider output and human approval have separate records. The baseline is suitable for dry runs, and a person must approve lyrics before using them as production text. A real provider may change the sung words, which reviewers must check by listening.

## Provider boundary and benchmark plan

`MusicProvider` exposes capability metadata, canonical translation and generation with a remote-start callback. Capabilities include text-to-music, lyrics, instrumental mode, vocals, requested duration, BPM, style, seed, stems, output formats, usage, provider request ID and rights information. The canonical brief does not assume a vendor can honor every control. Each future adapter must document its actual API and mark unsupported controls before live use.

There is **no configured real music provider** in this repository. `FakeMusicProvider` is a local deterministic PCM WAV synthesizer used for tests and optional offline runs. It does not sing the lyric. It can simulate success, preflight failure, safe retryable failure, ambiguous outcome, terminal failure and malformed output. No consumer UI automation or undocumented API is included.

Provider references in the earlier architecture note were checked against official documentation on 2026-09-25. [ElevenLabs Music API](https://elevenlabs.io/docs/api-reference/music/compose) documents prompt or composition-plan input and a requested duration, with paid API access described in its [quickstart](https://elevenlabs.io/docs/eleven-api/guides/cookbooks/music). [Google's Lyria 3.5 guide](https://ai.google.dev/gemini-api/docs/music-generation) describes a fixed 30-second Clip model and a longer model with prompt-influenced duration; its current outputs are MP3 by default. [Suno's official platform](https://platform.suno.com/auth/login?returnTo=%2F) advertises an API but leaves integration details behind sign-in. These are research inputs, not configured adapters or evidence of rights for this project. A real adapter also needs verified byte retention, format decoding, account-specific license evidence, credentials and separate live-call authorization.

The first real benchmark is three independent requests per configured provider from the same frozen brief and lyric candidate. Preserve all requests and outputs, including failures, and record the exact model, account tier, pricing and license evidence. Plan first, then separately authorize live generation after a documented adapter and credentials are present. Blind export omits provider and model; maintain the mapping in the request store. The operator should randomize presentation order before listening.

## Commands

From the repository root, with the example config:

```powershell
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark plan
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark run --offline-fake
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark status
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark reconcile --request-id ID
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark review-export
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark review --scorecard review.json
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark review-report
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark lyric-decision --lyrics-file benchmarks/music/colors_red_lyrics_v1.yaml --status approved --actor NAME --evidence "educational review"
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark decision --blind-id ID --type rights --status commercial_use_confirmed --actor NAME --evidence "license/tier evidence"
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark timing-import --blind-id ID --file timing.json
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark timing-decision --blind-id ID --version 1 --status approved --actor NAME --evidence "manual alignment check"
```

`plan` is credential-free and writes nothing. `run` currently permits only the explicitly named offline fake. Repeated runs reuse successful requests by deterministic fingerprint. A fresh data root is appropriate for each isolated dry-run. `status` includes provider identity for operators; `review-export` supplies blind IDs, brief IDs, duration and hash without provider identity. Scorecards contain `blind_id`, `reviewer`, eight integer `scores` (0–4), `evidence` and optional `hard_failures`.

## Durable state and recovery

Migration `0007_music_benchmark.sql` stores request ID, brief and lyric IDs, provider/model, attempt, canonical and translated specs, capabilities, fingerprint, status, provider request ID, failure category, timestamps, immutable receipt, output mapping, reviews, rights/approval decisions and timing versions. Migration `0009_music_audit_and_recovery_invariants.sql` enforces append-only reviews, decisions and timing history, valid decision type/status pairs, and matching receipt/output evidence before database success. A receipt records byte count, SHA-256, decoded duration, MIME, container, codec, usage, actual cost only when evidenced, pricing policy and provider rights evidence. Unknown cost stays SQL `NULL`, never zero by assumption. Validated returned WAV bytes are written to a request-scoped temporary sibling, flushed and `fsync`ed, then atomically renamed under `.music-returned`. The immutable receipt authenticates those staged bytes. Finalization verifies request identity, receipt, SHA-256, size and decoded WAV metadata, creates the final request-owned file and output mapping, and commits success with the mapping in one transaction. Staging is removed only after success commits. Each reuse verifies the retained bytes. A remote URL is never the only copy.

States are `prepared`, `remote_started`, `retryable_failure`, `ambiguous`, `terminal_failure` and `succeeded`. The adapter calls the remote-start callback immediately before a request could be sent. That callback commits the boundary before generation. Only a preflight failure with no recorded remote start may be run again. Even a provider-labelled retryable failure after remote start requires reconciliation or a new explicit attempt; it cannot be replayed blindly. `reconcile` is provider-free: a receipt plus matching staged or final bytes can complete a missing mapping or interrupted success transition, and repeats are idempotent. Staged or orphaned bytes without a receipt remain unresolved because their full returned-result metadata cannot be authenticated; the operator must investigate rather than regenerate. If a provider has asynchronous jobs, its eventual adapter must persist job IDs and use the documented status API before retrying. The fake exercises the same boundary offline.

The current ingest validates PCM WAV using the Python standard library. Other formats require a documented decoder and verification path before an adapter can use them. Decoded duration is recorded even when it misses the preferred range; a human then evaluates fitness. A corrupt result is terminal. Audio files and receipt rows are immutable; later decisions are append-only records.

## Listening, rights and selection

The [rubric](../benchmarks/music/rubric.v1.yaml) totals 100 points: educational correctness 20; lyric intelligibility 15; hook/memorability 15; preschool appropriateness 15; beat/timing usefulness 15; music/vocal quality 10; production fit 5; rights/provenance completeness 5. Give each axis 0–4, then calculate `sum(weight × score / 4)` for comparison. Record time-stamped listening evidence for low or exceptional scores. The software stores reviews; it does not choose a winner.

Hard failures include a false educational statement, unsafe lyrics, unintelligible teaching phrase, wrong concept, corrupt or unusable audio, incompatible license, or inability to retain the bytes. Compare duration fit, rhythmic regularity, clear downbeats, useful ending, production cleanliness and vocal quality. A reviewer must transcribe/check the actual sung teaching phrase; automatic speech recognition is only an aid. Resolve disagreements with a third human review. Report denominators, failures and unknown cost, not just scores.

Every output starts with rights `unknown` and approval `pending`, even if provider metadata contains a license claim. `rights` decisions require a named actor and evidence. Lyric decisions are append-only and tied to a hash of the exact candidate. Audio approval is a separate named human decision and requires `commercial_use_confirmed` rights, approved lyrics and two clean listening reviews. A later restrictive rights decision or rejection of the exact lyric ID and hash returns an approved candidate to pending and appends a system approval decision. Re-approving those lyrics never restores audio approval automatically; a new human audio decision is required. No review or score automatically approves or selects a candidate. The accepted song also requires an approved timing version before final storyboard work.

## Timing and animation handoff

Migration `0008_music_timing_decisions.sql` adds append-only human timing decisions.

`TimingAnalysis` is versioned and SHA-256 bound to one audio candidate. It has duration, estimated BPM, beats, downbeats, sections, lyric lines, optional word/phoneme intervals, accents, intro/outro ranges and correction attribution. Each import starts pending; a separate named timing decision with evidence records approval or rejection. Automatic alignment is an estimate; the editor can import a corrected new version without overwriting prior versions. Beat/downbeat and accent markers can drive bobs and claps; lyric word or phoneme spans can switch mouth sprites; gaps can admit blinks; section boundaries can trigger pointing, celebration and scene transitions. The first animation proof should use these same markers against the accepted audio. The final `TimedStoryboard` remains open until accepted audio and its reviewed timing are available.
