# Colors — Red music benchmark and policy gates

The normal path is **generate → machine evaluate → policy decision → continue or remain blocked**. A teacher, producer, named human, lyric approver, timing editor, or two listening reviewers are not required. People can inspect, review, reject, correct and manually approve as exceptional interventions. Every decision remains auditable. This work does not render or publish an episode.

## Canonical inputs and provider boundary

[`colors_red_v1.yaml`](../benchmarks/music/colors_red_v1.yaml) fixes the educational objective, red apple and ball examples, 30–40 second preferred duration, 45 second ceiling, 112 BPM target, sections and preschool style. [`colors_red_lyrics_v1.yaml`](../benchmarks/music/colors_red_lyrics_v1.yaml) retains the exact original words, claims and notes. Its `pending` field is not a mandatory human gate. Requested BPM is not measured audio timing.

`MusicProvider` describes capabilities, translates a frozen brief and lyric candidate, and calls a durable remote-start boundary before generation. A request persists its exact canonical and translated inputs, provider/model, fingerprint, outcome and provider request ID. `FakeMusicProvider` is a local PCM WAV synthesizer with no network call. It does not sing; fixture QA evidence only proves the offline policy path. `ElevenMusicProvider` is the live adapter described below. No LLM creative director, renderer, or publisher is configured.

The benchmark plans three independent candidates per provider. Blind export omits provider and model while preserving their mapping in the database. Human listening and [rubric](../benchmarks/music/rubric.v1.yaml) scorecards remain available for investigation and comparison; scores and reviews are optional. The rubric weights educational correctness, intelligibility, memorability, preschool suitability, beat usability, audio quality, production fit and rights provenance. A recorded human hard failure vetoes approval.

## Durable generation and recovery

Migration `0007_music_benchmark.sql` stores requests, immutable receipts, outputs, reviews and decisions. `0008_music_timing_decisions.sql` adds append-only timing decisions. `0009_music_audit_and_recovery_invariants.sql` enforces audit and receipt/output invariants. A receipt records SHA-256, byte count, decoded duration and format, optional usage and cost, provider metadata and rights evidence. Unknown cost stays SQL `NULL`, never an invented zero.

Validated original WAV or MP3 bytes are flushed and `fsync`ed in `.music-returned`, then atomically named before the receipt is committed. Finalization verifies receipt, bytes and decoded metadata, creates the final file and output mapping, and commits success transactionally. Staging is removed only after success. Reuse verifies retained bytes. Provider-free reconciliation can finish an interrupted request with a valid receipt. Staged bytes without a receipt stay unresolved. After a remote-start boundary, no blind retry is allowed. Migration `0011_music_audio_format_support.sql` extends the receipt/output path invariant to `.mp3` while preserving `.wav` for old receipts.

## Versioned policy evaluations

Migration `0010_music_policy_evaluations.sql` adds immutable, append-only records containing evaluation ID, subject type and exact ID/SHA-256, policy ID/version, evaluator identity and explicit `machine`/`human` type, `pass`/`fail`/`blocked` status, structured evidence, thresholds and timestamp. Decision tables also carry explicit actor type. The current policy IDs are `colors_red_lyrics`, `commercial_music_rights`, `music_qa`, `music_approval` and `music_timing`, all version 1. Later evaluator implementations must use a new policy version when semantics change.

The deterministic `colors_red_lyrics` policy checks the exact brief association and hash, “red is a color,” appropriate red apple and ball examples, configured contradiction patterns, line/word/section bounds and a small forbidden-name list. It records each check and the exact lyric hash. These text rules do not prove general semantic correctness, preschool safety, sung-word adherence or syllable duration. Additional semantic or transcription evaluators can supply versioned evidence later. Automatic approval requires a passing lyrics policy evaluation for the candidate's exact brief and lyrics; manual lyric decisions remain available.

Rights begin `unknown`. Automatic `commercial_use_confirmed` requires configured evidence matching the request's provider/model: account, product tier, terms version/date/source, a retained terms snapshot and matching SHA-256, commercial usage mode and an explicit commercial grant. Missing or adverse evidence leaves rights `unknown`. Provider metadata and LLM statements do not, by themselves, establish commercial rights. A configuration's authenticity must be established before any real provider is used. The automatic approval gate requires the latest rights decision to be a passing machine policy confirmation. Manual rights decisions remain explicit interventions.

Automated QA verifies retained original WAV or MP3 integrity, receipt identity and duration against the brief. It also requires structured check results with sources for lyric adherence, educational correctness, teaching intelligibility, preschool safety, beat usability, production fit and artifact absence, with zero hard failures. Not all those checks have real-audio evaluators yet: offline tests provide deterministic fixture results. A future real provider must supply trustworthy check provenance and actual vocal/audio analysis. An omitted or failing check blocks approval.

The music approval policy requires passing exact lyrics, rights and QA evaluations, confirmed rights, intact audio, no recorded hard failure and no active manual rejection. It appends a machine decision referencing the evaluation. A later failure of lyrics, rights, QA, review or detected artifact integrity returns an approved candidate to `pending` with a new reasoned decision. Prior approvals are never deleted. Passing again does not silently restore approval; rerun the approval policy to append a new decision. Manual approval also checks mandatory safety gates. A manual rejection vetoes automatic approval until a person explicitly clears it.

`TimingAnalysis` binds a version to an audio SHA-256 and stores duration, beats, downbeats, sections, lyric lines, optional words/phonemes, accents and intro/outro. The timing policy checks SHA and duration, ordered beat positions and required production spans, then appends a machine timing approval. Incomplete timing fails closed. A human can import a corrected version or make a manual timing decision. An approved machine timing version is a normal production input.

## Offline commands

Use the repository root and a local config:

```powershell
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark plan
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark run --offline-fake
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark status
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark reconcile --request-id ID
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark policy-evaluate --type lyrics --brief-file benchmarks/music/colors_red_v1.yaml --lyrics-file benchmarks/music/colors_red_lyrics_v1.yaml
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark policy-evaluate --type rights --blind-id ID --evidence-file rights.json
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark policy-evaluate --type qa --blind-id ID --evidence-file qa.json
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark policy-evaluate --type approval --blind-id ID
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark timing-import --blind-id ID --file timing.json
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark policy-evaluate --type timing --blind-id ID --version 1
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark policy-status --blind-id ID
```

Rights JSON requires `provider`, `model`, `tier`, `account_id`, `terms_version`, ISO `terms_date`, `terms_source`, `terms_snapshot`, SHA-256 `terms_sha256`, `usage_mode: commercial` and `commercial_use_allowed: true`. QA JSON requires each named check above as `{ "passed": true, "source": "..." }`; `hard_failures` is optional and must be empty to pass. These are evidence inputs, not a way to fabricate real licenses or real listening/transcription results. Commands are noninteractive and make no live provider call.

Manual commands remain: `review-export`, `review --scorecard`, `review-report`, `lyric-decision`, `decision` and `timing-decision`. They append evidence or intervention decisions; they are not prerequisites.

The intended full pipeline is **creative planner → lyrics policy → music generation → music QA → timing → visual planning → animation → final QA → release policy**. The current `music_approval` gate approves only an audio candidate. Full episode release policy, real sung-word analysis, authenticated rights configuration, visual/render integration remain future work.

## Eleven Music v2.5

The `elevenlabs` provider uses `model_id: music_v2_5` and one `POST https://api.elevenlabs.io/v1/music/detailed` request per benchmark attempt. It sends a deterministic composition plan, never a free-form `prompt` alongside that plan. The five chunks follow the committed brief's intro, hook, teaching, reinforcement and ending order, with durations of 3, 9, 10, 9 and 3 seconds (34 seconds total). Chunk text contains the exact committed lyric lines without rewriting them. Positive styles request bright, warm, bouncy preschool pop and approximately 112 BPM; BPM is a stylistic direction, not measured or enforced timing. For Music v2.5, the documented composition-plan section durations are enforced. The three planned candidates use distinct seeds; the API does not guarantee bit-for-bit reproducibility across service updates.

The detailed endpoint is requested with `with_timestamps: true` and `output_format=mp3_48000_192`. The latter is the documented v2 default; the original provider MP3 is retained without transcoding. The adapter requires a `multipart/mixed` response with exactly one JSON metadata part and one audio part. It retains the returned composition plan, song metadata and word timestamps as provider evidence. These timestamps do not prove sung-word adherence and do not automatically pass timing, intelligibility, or lyric QA. The `song-id` response header, when supplied, is stored as `provider_request_id`, distinct from the local request ID. The MP3 validator walks complete MPEG Layer III frames, derives duration from their sample counts, and rejects truncated or stray bytes. Receipts record MIME, container, codec, duration, byte count and SHA-256; original bytes use `.mp3` staging and final paths.

Set `ELEVENLABS_API_KEY` in the operator's local environment for a later authorized live run. Never put the key in Git configuration. The dry run does not need a key, create database requests, or call ElevenLabs:

```powershell
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark run --provider elevenlabs --dry-run
```

After separate authorization for paid generation, the normal durable runner can use `music-benchmark run --provider elevenlabs`. Eleven Music API access currently requires a paid ElevenLabs subscription. Local key and request validation finish before the durable `remote_started` transition; it is committed immediately before the single HTTP send. Missing key is a repairable local failure. Timeout, connection loss, HTTP 408/5xx, malformed successful responses and invalid returned audio are ambiguous. Ordinary deterministic 4xx responses, including 429, are terminal for this attempt and never trigger an automatic paid retry. Reconciliation never contacts ElevenLabs; a staged file without an immutable receipt remains unresolved for provider-side investigation.

Successful generation leaves rights `unknown` and approval `pending`. The account owner must provide one-time, account-specific rights evidence to the existing rights policy before commercial use can be confirmed. Store the actual provider/model, subscription or API tier, account/workspace identity, terms version and date, source URL, retained terms snapshot and its SHA-256, commercial usage mode, and a commercial grant supported by those terms. The current ElevenLabs tier has not been supplied, so no rights evidence is committed or assumed. Real sung-word, intelligibility and preschool audio QA also remain required before approval.

Official references: [detailed endpoint](https://elevenlabs.io/docs/api-reference/music/compose-detailed), [composition plans](https://elevenlabs.io/docs/eleven-api/guides/how-to/music/composition-plans), and [Music API availability](https://elevenlabs.io/docs/eleven-creative/products/music).
