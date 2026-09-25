# Colors — Red music benchmark and policy gates

The normal path is **generate → machine evaluate → policy decision → continue or remain blocked**. A teacher, producer, named human, lyric approver, timing editor, or two listening reviewers are not required. People can inspect, review, reject, correct and manually approve as exceptional interventions. Every decision remains auditable. This work does not render or publish an episode.

## Canonical inputs and provider boundary

[`colors_red_v1.yaml`](../benchmarks/music/colors_red_v1.yaml) fixes the educational objective, red apple and ball examples, 30–40 second preferred duration, 45 second ceiling, 112 BPM target, sections and preschool style. [`colors_red_lyrics_v1.yaml`](../benchmarks/music/colors_red_lyrics_v1.yaml) retains the exact original words, claims and notes. Its `pending` field is not a mandatory human gate. Requested BPM is not measured audio timing.

`MusicProvider` describes capabilities, translates a frozen brief and lyric candidate, and calls a durable remote-start boundary before generation. A request persists its exact canonical and translated inputs, provider/model, fingerprint, outcome and provider request ID. `FakeMusicProvider` is a local PCM WAV synthesizer with no network call. It does not sing; fixture QA evidence only proves the offline policy path. `VertexLyriaProvider` is a real adapter, but this PR performs no live generation. No LLM creative director, renderer, or publisher is configured.

## Vertex Lyria 3 Pro Preview

The Google provider uses Vertex AI's [Lyria 3 Pro Preview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/lyria/lyria-3) model ID `lyria-3-pro-preview` in the `global` location. It calls the [Interactions API](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/models/interactions-api) at `https://aiplatform.googleapis.com/v1beta1/projects/{PROJECT_ID}/locations/global/interactions`. `GOOGLE_CLOUD_PROJECT` supplies the project (`tovitunes` for this deployment); `GOOGLE_CLOUD_LOCATION`, if set, must be `global`. Live calls use Google Application Default Credentials with the cloud-platform OAuth scope. No Gemini API key is used. Missing project, invalid location or failed ADC load/refresh is a local preflight failure before the generation boundary.

For each of the three `colors_red_v1` candidates, translation retains the exact committed brief and lyric candidate and persists a deterministic prompt and request body in the fingerprint. The request body uses only the documented `model`, text `input`, and `store: true` fields. The prompt gives a roughly 34-second, 112 BPM direction and a 3-second intro, 9-second hook, 10-second teaching section, 9-second reinforcement and 3-second ending. It calls for a clear English preschool vocal and original music without named artist or song imitation. Following the [official supplied-lyrics convention](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/music/music-gen-prompt-guide), `Lyrics:` is followed by the exact canonical lines. Provider-returned lyrics and description remain separate evidence; neither replaces the canonical lyrics. These prompt directions do not guarantee exact duration, tempo or lyric adherence.

`music-benchmark plan --provider google` and `music-benchmark run --provider google --dry-run` print three plans without loading ADC or contacting Google. A future `music-benchmark run --provider google` uses the normal durable runner. The first live Lyria generation requires separate authorization after review and merge. This PR makes **zero live Lyria generation requests**.

The benchmark plans three independent candidates per provider. Blind export omits provider and model while preserving their mapping in the database. Human listening and [rubric](../benchmarks/music/rubric.v1.yaml) scorecards remain available for investigation and comparison; scores and reviews are optional. The rubric weights educational correctness, intelligibility, memorability, preschool suitability, beat usability, audio quality, production fit and rights provenance. A recorded human hard failure vetoes approval.

## Durable generation and recovery

Migration `0007_music_benchmark.sql` stores requests, immutable receipts, outputs, reviews and decisions. `0008_music_timing_decisions.sql` adds append-only timing decisions. `0009_music_audit_and_recovery_invariants.sql` enforces audit and receipt/output invariants. `0011_music_audio_format_support.sql` generalizes the mapping constraints to verified `.wav`/WAV and `.mp3`/MP3 originals while retaining the SHA, request-identity and receipt invariants. A receipt records SHA-256, byte count, independently decoded duration and format, measured sample rate and bitrate where available, optional usage and cost, provider metadata and rights evidence. Unknown cost stays SQL `NULL`, never an invented zero.

Validated returned WAV or MP3 bytes are flushed and `fsync`ed in `.music-returned`, then atomically named before the receipt is committed. The exact provider MP3 is retained byte-for-byte; it is never transcoded for the receipt. Finalization verifies receipt, bytes and independently decoded metadata, creates the final file and output mapping, and commits success transactionally. Staging is removed only after success. Reuse verifies retained bytes. Provider-free `music-benchmark reconcile` can finish an interrupted request with a valid receipt. Staged bytes without a receipt stay unresolved locally. MP3 checks use Mutagen metadata and full miniaudio frame decoding to reject empty, corrupt, undecodable or implausibly long files where detectable. PCM WAV remains supported.

For Google, the runner commits `remote_started` immediately before the one generation POST and commits the returned `interaction.id` separately as `provider_request_id` as soon as it can parse it. There is at most **one generation POST per logical attempt** and no automatic generation POST retry. Connection loss, timeout, HTTP 408, 429 and 5xx leave the outcome ambiguous; known ordinary 4xx are terminal. An interaction may be `in_progress`, `requires_action`, `completed`, `failed`, `cancelled` or `incomplete`. A completed result must have exactly one `audio/mpeg` audio output with valid base64 and decodable MP3. The adapter retains provider text outputs and any returned usage; actual billing cost remains unknown unless supplied by a trustworthy response.

`music-benchmark provider-resume --request-id ID` can perform a GET of the **stored** interaction ID to retrieve its status and, when complete, finish ingestion. Invoke it again if the same interaction remains in progress; each invocation makes at most one GET. It never makes a generation POST. The provider-free `reconcile` command never contacts Google. Neither operation creates a fresh interaction to resolve an ambiguous POST whose ID was not returned.

## Versioned policy evaluations

Migration `0010_music_policy_evaluations.sql` adds immutable, append-only records containing evaluation ID, subject type and exact ID/SHA-256, policy ID/version, evaluator identity and explicit `machine`/`human` type, `pass`/`fail`/`blocked` status, structured evidence, thresholds and timestamp. Decision tables also carry explicit actor type. The current policy IDs are `colors_red_lyrics`, `commercial_music_rights`, `music_qa`, `music_approval` and `music_timing`, all version 1. Later evaluator implementations must use a new policy version when semantics change.

The deterministic `colors_red_lyrics` policy checks the exact brief association and hash, “red is a color,” appropriate red apple and ball examples, configured contradiction patterns, line/word/section bounds and a small forbidden-name list. It records each check and the exact lyric hash. These text rules do not prove general semantic correctness, preschool safety, sung-word adherence or syllable duration. Additional semantic or transcription evaluators can supply versioned evidence later. Automatic approval requires a passing lyrics policy evaluation for the candidate's exact brief and lyrics; manual lyric decisions remain available.

Rights begin `unknown`. Automatic `commercial_use_confirmed` requires configured evidence matching the request's provider/model: account, product tier, terms version/date/source, a retained terms snapshot and matching SHA-256, commercial usage mode and an explicit commercial grant. Missing or adverse evidence leaves rights `unknown`. Provider metadata and LLM statements do not, by themselves, establish commercial rights. A configuration's authenticity must be established before any real provider is used. The automatic approval gate requires the latest rights decision to be a passing machine policy confirmation. Manual rights decisions remain explicit interventions.

Automated QA verifies retained audio integrity, receipt identity and duration against the brief. It also requires structured check results with sources for lyric adherence, educational correctness, teaching intelligibility, preschool safety, beat usability, production fit and artifact absence, with zero hard failures. Not all those checks have real-audio evaluators yet: offline tests provide deterministic fixture results. Lyria output needs trustworthy check provenance and actual vocal/audio analysis. An omitted or failing check blocks approval. Lyria does not supply documented word or phoneme timestamps here; timing evidence remains incomplete until audio analysis produces it.

The music approval policy requires passing exact lyrics, rights and QA evaluations, confirmed rights, intact audio, no recorded hard failure and no active manual rejection. It appends a machine decision referencing the evaluation. A later failure of lyrics, rights, QA, review or detected artifact integrity returns an approved candidate to `pending` with a new reasoned decision. Prior approvals are never deleted. Passing again does not silently restore approval; rerun the approval policy to append a new decision. Manual approval also checks mandatory safety gates. A manual rejection vetoes automatic approval until a person explicitly clears it.

Every generated Lyria artifact begins with rights `unknown` and approval `pending`. Lyria is a Preview offering; successful generation does not establish commercial rights. The existing rights policy requires retained, applicable terms evidence for this project's Google Cloud agreement before automatic commercial confirmation. No such evidence or sung-word QA is fabricated here.

`TimingAnalysis` binds a version to an audio SHA-256 and stores duration, beats, downbeats, sections, lyric lines, optional words/phonemes, accents and intro/outro. The timing policy checks SHA and duration, ordered beat positions and required production spans, then appends a machine timing approval. Incomplete timing fails closed. A human can import a corrected version or make a manual timing decision. An approved machine timing version is a normal production input.

## Offline commands

Use the repository root and a local config:

```powershell
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark plan
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark plan --provider google
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark run --provider google --dry-run
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark run --offline-fake
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark status
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark reconcile --request-id ID
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark provider-resume --request-id ID
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark policy-evaluate --type lyrics --brief-file benchmarks/music/colors_red_v1.yaml --lyrics-file benchmarks/music/colors_red_lyrics_v1.yaml
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark policy-evaluate --type rights --blind-id ID --evidence-file rights.json
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark policy-evaluate --type qa --blind-id ID --evidence-file qa.json
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark policy-evaluate --type approval --blind-id ID
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark timing-import --blind-id ID --file timing.json
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark policy-evaluate --type timing --blind-id ID --version 1
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark policy-status --blind-id ID
```

Rights JSON requires `provider`, `model`, `tier`, `account_id`, `terms_version`, ISO `terms_date`, `terms_source`, `terms_snapshot`, SHA-256 `terms_sha256`, `usage_mode: commercial` and `commercial_use_allowed: true`. QA JSON requires each named check above as `{ "passed": true, "source": "..." }`; `hard_failures` is optional and must be empty to pass. These are evidence inputs, not a way to fabricate real licenses or real listening/transcription results. The Google plan and dry-run commands make no provider call; `provider-resume` makes a status GET for an existing interaction ID.

Manual commands remain: `review-export`, `review --scorecard`, `review-report`, `lyric-decision`, `decision` and `timing-decision`. They append evidence or intervention decisions; they are not prerequisites.

The intended full pipeline is **creative planner → lyrics policy → music generation → music QA → timing → visual planning → animation → final QA → release policy**. The current `music_approval` gate approves only an audio candidate. Full episode release policy, real sung-word analysis, authenticated rights configuration and visual/render integration remain future work.
