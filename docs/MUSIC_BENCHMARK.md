# Colors — Red music benchmark and policy gates

The normal path is **generate → machine evaluate → policy decision → continue or remain blocked**. A teacher, producer, named human, lyric approver, timing editor, or two listening reviewers are not required. People can inspect, review, reject, correct and manually approve as exceptional interventions. Every decision remains auditable. This work does not render or publish an episode.

## Canonical inputs and provider boundary

[`colors_red_v1.yaml`](../benchmarks/music/colors_red_v1.yaml) fixes the educational objective, red apple and ball examples, 30–40 second preferred duration, 45 second ceiling, 112 BPM target, sections and preschool style. [`colors_red_lyrics_v1.yaml`](../benchmarks/music/colors_red_lyrics_v1.yaml) retains the exact original words, claims and notes. Its `pending` field is not a mandatory human gate. Requested BPM is not measured audio timing.

`MusicProvider` describes capabilities, translates a frozen brief and lyric candidate, and calls a durable remote-start boundary before generation. A request persists its exact canonical and translated inputs, provider/model, fingerprint, outcome and provider request ID. `FakeMusicProvider` is a local PCM WAV synthesizer with no network call. It does not sing; fixture QA evidence only proves the offline policy path. `VertexLyriaProvider` is a real adapter, but this PR performs no live generation. No LLM creative director, renderer, or publisher is configured.

## Vertex Lyria 3 Pro Preview

The Google provider uses Vertex AI's [Lyria 3 Pro Preview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/lyria/lyria-3) model ID `lyria-3-pro-preview` in the `global` location. It sends a synchronous generation POST to the [Lyria-specific REST endpoint](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/music/generate-music) at `https://aiplatform.googleapis.com/v1beta1/projects/{PROJECT_ID}/locations/global/interactions`. `GOOGLE_CLOUD_PROJECT` supplies the resource project (`tovitunes` for this deployment); `GOOGLE_CLOUD_LOCATION`, if set, must be `global`. Optional `GOOGLE_CLOUD_QUOTA_PROJECT` selects the quota project and defaults to `GOOGLE_CLOUD_PROJECT`. The safe quota project ID is stored with the translated request. Direct generation POST and conditional known-ID retrieval GET requests include `x-goog-user-project` with that ID. The user must have permission to use the selected quota project. Live calls use Google Application Default Credentials with the cloud-platform OAuth scope. The ADC file itself does not need a quota project or any rewrite for this adapter; optionally, an operator may run `gcloud auth application-default set-quota-project tovitunes`. The adapter never runs that command. No Gemini API key is used. Missing or invalid project or quota project, invalid location, or failed ADC load/refresh is a local preflight failure before the generation boundary.

### Original contract

The first real generation used coarse section timing and one ordinary lyrics block. Retained request `451ba192-da15-46d3-b5fd-39712190c838` produced a 58.018-second artifact, repeated reinforcement material, and omitted canonical line 2. Three independent Whisper analyses (`small.en`, `medium.en`, `large-v3`) corroborated this structural deviation: 36 canonical words, 54 recognized words, 30 matches, 24 insertions, 6 deletions, no substitutions, WER and coverage both 0.8333333333333334. The classification is `CONFIRMED_GENERATION_LYRIC_DEVIATION`. The next experiment targets generation control; no additional ASR model is required.

### Exact-lyrics v2

New translations record `prompt_contract: lyria_exact_lyrics_v2` as ToviTunes provenance. It participates in the deterministic fingerprint, but is never sent to Google: the HTTP body still contains exactly `model` and `input`, using the same `lyria-3-pro-preview` model. Preflight rejects absent or incompatible stored contract identities before ADC or HTTP. Historical requests are not rewritten or upgraded; their stored contracts remain historical evidence.

Following Google's [timeline sequence and supplied-lyrics guidance](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/music/music-gen-prompt-guide), one authoritative `Lyrics:` block assigns each canonical line a `[mm:ss]` marker. Each complete canonical line appears exactly once in the whole prompt. The fixed schedule uses instrumental intro at 00:00, line starts at 00:03, 00:07, 00:11, 00:16, 00:21, 00:25 and 00:31, vocal cutoff at 00:33 and track end at approximately 00:34. English, ages 3–6, approximately 112 BPM, bright warm preschool pop and clear teaching vocals remain the directions.

The performance contract requires each line exactly once in canonical order, forbids repetition, omission, invented sung words, worded ad-libs, chorus/refrain repetition and restarting earlier material, and permits no additional vocal words after the final lyric. After the vocal cutoff, only a short instrumental ending is allowed. A structured internal timeline validates seven exact canonical lines and their sections, strictly increasing timestamps, intro/ending boundaries, the preferred duration range and canonical maximum. Unexpected text, count, order, required-section or impossible-duration changes fail locally rather than silently changing the schedule.

The new prompt and provenance produce a new fingerprint; the readiness report records the deterministic candidate-2 identity. Historical candidate 1 retains `066ebae73bc548bf1d66500b17e50b48de13bd42aef07ce9288cfd0a8aca22cb`. Provider-returned lyrics and descriptions remain separate evidence. These instructions improve control but **do not guarantee provider compliance**. Real ASR and independently measured QA remain authoritative. The 45-second ceiling, lyric/ASR/alignment thresholds, rights and approval policies remain unchanged.

`music-benchmark plan --provider google --attempt 2` and `music-benchmark run --provider google --attempt 2 --dry-run` each print exactly one candidate without loading ADC, contacting Google or creating database rows. The next intended paid experiment is attempt 2, but requires separate authorization. This PR executes only planning and dry-run, with **zero live generation requests and zero provider-resume GETs**.

The benchmark plans three independent candidates per provider. Blind export omits provider and model while preserving their mapping in the database. Human listening and [rubric](../benchmarks/music/rubric.v1.yaml) scorecards remain available for investigation and comparison; scores and reviews are optional. The rubric weights educational correctness, intelligibility, memorability, preschool suitability, beat usability, audio quality, production fit and rights provenance. A recorded human hard failure vetoes approval.

## Durable generation and recovery

Migration `0007_music_benchmark.sql` stores requests, immutable receipts, outputs, reviews and decisions. `0008_music_timing_decisions.sql` adds append-only timing decisions. `0009_music_audit_and_recovery_invariants.sql` enforces audit and receipt/output invariants. `0011_music_audio_format_support.sql` generalizes the mapping constraints to verified `.wav`/WAV and `.mp3`/MP3 originals while retaining the SHA, request-identity and receipt invariants. A receipt records SHA-256, byte count, independently decoded duration and format, measured sample rate and bitrate where available, optional usage and cost, provider metadata and rights evidence. Unknown cost stays SQL `NULL`, never an invented zero.

Validated returned WAV or MP3 bytes are flushed and `fsync`ed in `.music-returned`, then atomically named before the receipt is committed. The exact provider MP3 is retained byte-for-byte; it is never transcoded for the receipt. Finalization verifies receipt, bytes and independently decoded metadata, creates the final file and output mapping, and commits success transactionally. Staging is removed only after success. Reuse verifies retained bytes. Provider-free `music-benchmark reconcile` can finish an interrupted request with a valid receipt. Staged bytes without a receipt stay unresolved locally. MP3 checks use Mutagen metadata and full miniaudio frame decoding to reject empty, corrupt, undecodable or implausibly long files where detectable. PCM WAV remains supported.

For Google, the runner commits `remote_started` immediately before the one generation POST. A top-level provider interaction ID is not guaranteed by the Lyria-specific completed-response example. When Google supplies a valid ID, the runner commits it separately as `provider_request_id` as soon as it can parse it and enforces identity consistency. A completed response can succeed without one when it has exactly one `audio/mpeg` audio output with valid base64 and decodable MP3. In that case `provider_request_id` stays SQL `NULL`, the local request UUID remains the durable identity, and safe receipt metadata records that no provider ID was supplied. Exact provider-original MP3 bytes are staged and receipted before output mapping. There is at most **one generation POST per logical attempt** and no automatic generation POST retry. Connection loss, timeout, HTTP 408, 429 and 5xx leave the outcome ambiguous; known ordinary 4xx are terminal. For structured Google errors, the durable failure reason includes only bounded safe code, status and message fields; malformed or unsafe details leave the generic HTTP reason. The eight known interaction statuses are `queued`, `in_progress`, `requires_action`, `completed`, `failed`, `cancelled`, `incomplete` and `budget_exceeded`. A pending response without a durable provider ID stays ambiguous and cannot be provider-resumed. Known terminal failures (`failed`, `cancelled`, `incomplete`, `budget_exceeded`) retain a supplied ID and reason without retrying generation. The adapter retains provider text outputs and any returned usage; actual billing cost remains unknown unless supplied by a trustworthy response.

`music-benchmark provider-resume --request-id ID` is conditional retrieval for a trustworthy **stored** interaction ID with the current prompt-contract identity. Incompatible historical contracts fail local preflight without a GET; they are never retrofitted. It is not a guaranteed recovery path for new Lyria requests: the Lyria-specific contract does not guarantee an ID or retrievability. If a known ID is retrievable, the command can perform a GET to check status and, when complete, finish ingestion. Each invocation makes at most one GET. A returned `budget_exceeded` transitions the stored request to terminal failure. It never makes a generation POST. The provider-free `reconcile` command never contacts Google. Neither operation creates a fresh interaction to resolve an ambiguous POST whose ID was not returned.

The first authorized historical live request `413d085b-f45b-4de6-8687-1ad7816c71de` remains a `terminal_failure` after one HTTP 400 POST, with no provider ID, receipt, audio or output and no automatic retry. Its original error body was not retained, so the exact cause of that 400 is unknown. This change does not modify its durable history or attach a retrospective diagnostic.

## Offline analysis of retained music

`music-benchmark analyze-audio --blind-id ID --analysis-version 1` reads the retained file only through its authoritative SQLite output mapping. It verifies receipt, byte count, decoded format, duration, and SHA-256 before analysis and again before committing. Migration `0012_music_audio_analysis.sql` stores immutable, versioned JSON reports bound to the request, blind ID, and original audio SHA. The command also stores a **pending** `TimingAnalysis` candidate and records a version-2 machine QA evaluation. Reusing the same version, audio SHA, and analyzer configuration returns the stored report; changing inputs requires a new version. The original MP3 is never edited or replaced.

The report separates technical PCM measurements, independent WhisperX recognition, canonical forced alignment, lyric edit operations, and librosa beat tracking. It records analyzer/library versions, requested model, execution device, timestamp, and source SHA. The independent ASR transcript is evidence about words heard by the model. Canonical forced alignment estimates where *expected* words occur, but cannot prove they were sung. Timed words and lines enter the timing candidate only when independent ASR coverage is at least 0.85 and WER is at most 0.25. No phonemes or downbeats are inferred in V1. The existing timing policy therefore fails when it requires downbeats. Requested prompt section times and 112 BPM are directions, not measured evidence; sections are inferred only from aligned lyric lines. Unknown or failed transcription and alignment stay incomplete.

Technical measurements use decoded float PCM: frame count and duration, sample rate, channels, peak, RMS, DC offset, samples at or above 0.999 amplitude, and 10 ms near-silence windows at -50 dBFS. No LUFS value is reported without an integrated-loudness implementation. Beat tracking uses `librosa.beat.beat_track`; its measured tempo, beat times, and interval variation are compared to the committed 100–124 BPM range. If librosa is absent or cannot establish beats, rhythm remains unavailable. These checks cover measurable digital defects only; they do not establish the absence of all perceptual music artifacts.

The version-2 QA evaluation derives its seven dimensions from the persisted report and retains explicit thresholds in each policy record. Literal recognized phrases, word edits, CTC alignment scores, technical metrics, and beat measurements are linked in the evidence. CTC scores are alignment scores, not calibrated ASR recognition probabilities. The configured safety vocabulary check covers only `kill`, `gun`, `knife`, and `hate`, plus unexpected-word burden; it is not a general preschool-safety classifier. A broad `artifact_free` pass is not asserted from technical measurements alone. Any unknown dimension fails closed. All automatic music approvals require version-2 QA rather than legacy supplied booleans. Rights and approval remain separate decisions.

For a Python 3.11 analysis environment, install optional groups with `uv sync --python 3.11 --extra dev --extra audio-analysis --extra audio-asr`. WhisperX does not use diarization or require a diarization token. The CLI accepts `--device auto|cpu|cuda` and `--asr-model MODEL`. Model and tokenizer downloads require explicit `--allow-model-download`; otherwise model loading uses local files only. Caches live under the retained audio directory's `.analysis-models`, and download permission is recorded. Timestamp interpolation is disabled: missing aligned words make evidence incomplete. With no local ASR model or optional dependencies, the command still persists technical evidence and an honest incomplete report. A failed model load or alignment is recorded as incomplete; no timestamps or pass result are invented. `music-benchmark policy-evaluate --type timing --blind-id ID --version N` runs the timing policy separately when requested. The analysis command's successful exit means evidence was persisted, not that QA passed.

The analyzer configuration fingerprint includes normalized source-code SHA-256 and installed analysis-library versions. Model revisions or weight hashes are retained when discoverable; unknown revisions remain null. A changed implementation, library environment, or model selection requires an explicit new analysis version. Migration `0013_music_analysis_identity.sql` enforces that each inserted report's request, blind ID, SHA, configuration SHA, and version match its retained output and indexed row. Timing admission requires adequate independent word coverage and WER plus a minimum CTC score of 0.5 for every canonical word. Intro/outro remain unavailable unless actual measured intervals exist; requested prompt boundaries never supply them.

The retained real Lyria candidate `mb_49286b900bcb4dd69f84fd857bbae7ef` measures about 58.018 seconds against a 45-second canonical maximum. `duration_in_scope` must therefore be false and music QA must fail, irrespective of other observations. Do not trim or transcode the provider-original MP3 to change that result.

## Versioned policy evaluations

Migration `0010_music_policy_evaluations.sql` adds immutable, append-only records containing evaluation ID, subject type and exact ID/SHA-256, policy ID/version, evaluator identity and explicit `machine`/`human` type, `pass`/`fail`/`blocked` status, structured evidence, thresholds and timestamp. Decision tables also carry explicit actor type. `music_qa` and `music_approval` now use version 2 for the analysis-based automatic path. Legacy QA version 1 remains an explicitly supplied-evidence interface, but cannot satisfy automatic approval. `colors_red_lyrics`, `commercial_music_rights`, and `music_timing` remain version 1. Later evaluator implementations must use a new policy version when semantics change.

The deterministic `colors_red_lyrics` policy checks the exact brief association and hash, “red is a color,” appropriate red apple and ball examples, configured contradiction patterns, line/word/section bounds and a small forbidden-name list. It records each check and the exact lyric hash. These text rules do not prove general semantic correctness, preschool safety, sung-word adherence or syllable duration. Additional semantic or transcription evaluators can supply versioned evidence later. Automatic approval requires a passing lyrics policy evaluation for the candidate's exact brief and lyrics; manual lyric decisions remain available.

Rights begin `unknown`. Automatic `commercial_use_confirmed` requires configured evidence matching the request's provider/model: account, product tier, terms version/date/source, a retained terms snapshot and matching SHA-256, commercial usage mode and an explicit commercial grant. Missing or adverse evidence leaves rights `unknown`. Provider metadata and LLM statements do not, by themselves, establish commercial rights. A configuration's authenticity must be established before any real provider is used. The automatic approval gate requires the latest rights decision to be a passing machine policy confirmation. Manual rights decisions remain explicit interventions.

Automated QA verifies retained audio integrity, receipt identity and duration against the brief. It derives lyric adherence, literal educational phrase evidence, teaching intelligibility proxies, vocabulary safety evidence, beat usability, production fit and digital artifact observations from the persisted report. Missing or insufficient evidence cannot pass. The compatible `artifact_free` field means no configured objective digital defect detected by deterministic checks; it does not assess universal perceptual perfection. Rights and approval remain separate gates. Lyria does not supply documented word or phoneme timestamps here; provider text cannot substitute for independent recognition or measured timing.

The music approval policy requires passing exact lyrics, rights and QA evaluations, confirmed rights, intact audio, no recorded hard failure and no active manual rejection. It appends a machine decision referencing the evaluation. A later failure of lyrics, rights, QA, review or detected artifact integrity returns an approved candidate to `pending` with a new reasoned decision. Prior approvals are never deleted. Passing again does not silently restore approval; rerun the approval policy to append a new decision. Manual approval also checks mandatory safety gates. A manual rejection vetoes automatic approval until a person explicitly clears it.

Every generated Lyria artifact begins with rights `unknown` and approval `pending`. Lyria is a Preview offering; successful generation does not establish commercial rights. The existing rights policy requires retained, applicable terms evidence for this project's Google Cloud agreement before automatic commercial confirmation. No such evidence or sung-word QA is fabricated here.

`TimingAnalysis` binds a version to an audio SHA-256 and stores duration, beats, downbeats, sections, lyric lines, optional words/phonemes, accents and intro/outro. The timing policy checks SHA and duration, ordered beat positions and required production spans, then appends a machine timing approval. Incomplete timing fails closed. A human can import a corrected version or make a manual timing decision. An approved machine timing version is a normal production input.

## Offline commands

Use the repository root and a local config:

```powershell
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark plan
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark plan --provider google
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark plan --provider google --attempt 1
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark run --provider google --dry-run
uv run python -m tovitunes.cli --config config.example.yaml music-benchmark run --provider google --attempt 2 --dry-run
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

## Operational ASR runtime (Windows / Python 3.11)

Install the optional groups from the unchanged lock, then use `--no-sync` so subsequent commands do not remove the extras:

```powershell
uv sync --python 3.11 --locked --extra dev --extra audio-analysis --extra audio-asr
uv run --no-sync python -m tovitunes.cli --config <CONFIG> music-benchmark analysis-doctor --device cpu
uv run --no-sync python -m tovitunes.cli --config <CONFIG> music-benchmark analysis-models prepare --asr-model small.en --device cpu --allow-model-download
uv run --no-sync python -m tovitunes.cli --config <CONFIG> music-benchmark analysis-doctor --device cpu
uv run --no-sync python -m tovitunes.cli --config <CONFIG> music-benchmark analyze-audio --blind-id <ID> --analysis-version <UNUSED_VERSION> --asr-model small.en --device cpu
uv run --no-sync python -m tovitunes.cli --config <CONFIG> music-benchmark policy-evaluate --type timing --blind-id <ID> --version <UNUSED_VERSION>
```

Doctor emits JSON for Python/platform, package versions, FFmpeg status/path/version, CPU/CUDA/device, project cache presence and offline readiness. It does not open or migrate SQLite, create analysis artifacts, download models, or contact providers. Readiness is a preflight; inference validates model integrity. FFmpeg must work on PATH; unavailable or failed executables produce an explicit diagnosis. No FFmpeg binary is bundled.

Preparation is the explicit networked phase and requires `--allow-model-download`. It obtains only `small.en`, English `WAV2VEC2_ASR_BASE_960H`, and NLTK `punkt_tab`; it also inventories WhisperX's bundled pyannote voice-activity model. No diarization or auth token is required. There is no automatic retry with a larger model. CPU is the required baseline. CUDA is an explicit optional choice on compatible installations, never an automatic retry.

Caches are under `<data_root>/music-benchmark/.analysis-models`: `asr` holds the Systran HF snapshot/ref, `alignment` holds English TorchAudio weights, and `nltk` holds tokenizer resources. `hf` and `torch` isolate auxiliary library roots. Global user caches are not authoritative. `inventory.json` records timestamp, source families, logical model names, resolved ASR revision, package versions, paths, sizes and file SHA-256 hashes. Unknown revisions remain null. Repeating preparation reports reuse and validates cache-only loading; unchanged inventoried files are not repeatedly hashed. Analysis never rehashes large alignment weights.

After preparation, omit `--allow-model-download` for offline analysis. HF/Transformers offline flags are set; faster-whisper receives the cached snapshot path and `local_files_only`; English alignment is preflighted and loaded cache-only; NLTK searches only the project resources. An outbound socket guard also closes TorchAudio's downloader path, which does not honor WhisperX's cache-only flag for its bundle. Guards/settings are scoped and restored. Run the CLI in its own process rather than concurrently with unrelated networking. Missing/incomplete assets fail closed. Bounded stage-specific diagnostics omit arbitrary exception messages, signed URLs and credentials.

Independent ASR answers what the model heard, preserving the exact text for deterministic WER, coverage and phrase checks. Separate canonical forced alignment answers where expected lyrics align, and cannot prove that those lyrics were sung correctly. No timestamp interpolation is used. Missing canonical timestamps remain absent and are listed in `missing_words`; only observed words and fully aligned lines survive, and timing admission fails. Word scores are CTC alignment scores, not recognition confidence.

Choose an unused analysis version: historical evidence is immutable and changed code/configuration needs a new version. Timing policy runs separately. Production beats and downbeats now use the optional Beat This detector described below, never synthetic every-fourth-beat inference. Missing detector assets fail timing closed. All duration, WER, coverage and score thresholds remain unchanged. Analysis/preparation cannot approve music or change rights.

## Production beat/downbeat timing

Install the released `beat-this==1.1.0` through the separate `audio-timing` extra.
The public `beat_this.inference.Audio2Beats` adapter consumes the verified decoded
PCM and its actual sample rate, so timing adds no MP3 decode. It supplies both
beat and downbeat arrays. BPM is 60 divided by their measured mean beat interval;
interval mean, population standard deviation and CV use that same beat clock.
No secondary beat grid is mixed in. `dbn=False` is mandatory; madmom and Torch Hub
repository-code execution are not used. FP16 is enabled only for CUDA, and CPU
is supported for diagnosis. Minimal installation, configuration and CI require
neither Beat This nor Torch nor CUDA.

```text
uv sync --python 3.11 --locked --extra dev --extra audio-analysis --extra audio-asr --extra audio-timing
uv run --no-sync python -m tovitunes.cli --config <CONFIG> music-benchmark analysis-models prepare-timing --device cuda --allow-model-download
# Run again to verify action=reused and offline_load_validated=true.
uv run --no-sync python -m tovitunes.cli --config <CONFIG> music-benchmark analysis-doctor --asr-model large-v3 --device cuda
uv run --no-sync python -m tovitunes.cli --config <CONFIG> music-benchmark analyze-audio --blind-id <ID> --analysis-version <NEXT_FREE_VERSION> --asr-model large-v3 --device cuda
uv run --no-sync python -m tovitunes.cli --config <CONFIG> music-benchmark policy-evaluate --type timing --blind-id <ID> --version <NEXT_FREE_VERSION>
```

For the already validated Windows CUDA environment, use its dedicated Python
launcher and install only `beat-this==1.1.0` with `uv pip --python <ENV_PYTHON>`.
Do not sync over torch/torchaudio 2.8.0+cu128, WhisperX 3.8.6, Faster-Whisper 1.2.1
or CTranslate2 4.8.2. The [CUDA verification guide](LARGE_V3_CUDA_ASR_FINAL_VERIFICATION.md)
documents process-local DLL selection. Model preparation requires explicit
network permission and does not invoke generation or provider-resume.

Timing preparation downloads only the upstream `final0` checkpoint, retaining it
at `<data_root>/music-benchmark/.analysis-models/torch/hub/checkpoints/beat_this-final0.ckpt`.
`timing-inventory.json` independently records the package version, logical model,
source URL, SHA-256, byte size, action, device and offline load validation; ASR's
inventory remains intact. Subsequent preparation reuses the asset. Inventoried
content changes fail closed. Runtime verifies the current file hash/size and
inventory identity, passes only its absolute local path, and blocks outbound
sockets and upstream's implicit checkpoint URL fallback. Analysis never receives
the short name `final0` and has no timing-download switch. Missing package,
checkpoint or verified inventory records unavailable rhythm with no fabricated
downbeats. It does not break unrelated CLI commands.

Doctor reports a separate `timing` object with installed version, checkpoint
path/cache/hash, requested device, local model-load readiness and offline timing
readiness. The existing CUDA fields report CUDA availability. The doctor runs
local timing load validation without network access and never searches global
untracked caches. Its existing top-level `offline_ready` remains ASR readiness;
production requires both it and `timing.offline_timing_ready`.

Rhythm provenance retains Beat This/package/model/checkpoint SHA, device, FP16,
DBN=false and source audio SHA. Ordered, finite, bounded detector arrays are
required; detected downbeats must lie on that detector's measured beat grid.
Historical rhythm JSON without `downbeat_seconds` or provenance loads with
defaults. Historical analysis, timing and provenance rows are never rewritten.

Only canonical words admitted by the existing coverage >=0.85, WER <=0.25 and
every-word score >=0.5 thresholds supply production word/line/section timing.
Their first start gives the **pre-lyric interval** `[0, first_word.start]`, and
their last end gives the **post-lyric interval** `[last_word.end, actual_duration]`.
Zero-length edges remain absent. These are measured non-lyric edge regions, not
claims of purely instrumental sound. No generation-prompt schedule, interpolation,
word-specific exception or manual endpoint is used. Production fit also requires
positive measured edge ranges, complete rhythm/alignment, admitted words/lines/
sections, duration <= the canonical maximum and existing technical/edge-silence
gates. Objective artifact checks fail for invalid PCM/decode evidence, clipping,
excessive silence or dropout; verified clear evidence passes, unavailable source
verification is unknown. No aesthetic scoring is added.

### Dependency and license provenance

The released [Beat This project](https://github.com/CPJKU/beat_this/tree/v1.1.0)
and [PyPI package](https://pypi.org/project/beat-this/1.1.0/) are reused directly;
no network implementation is copied. The installed wheel's `inference.py` and
minimal postprocessor were inspected to validate local-path fallback behavior,
FP16 support, checkpoint naming and `(beats, downbeats)` tuple order. Focused
adapter tests use distinguishable arrays, and the production probe checks the
installed postprocessor itself.

Upstream releases code and published model weights under MIT. Its attribution
and license are retained in [BEAT_THIS_LICENSE.txt](BEAT_THIS_LICENSE.txt).
Upstream also warns that some training files are copyrighted or under limited
Creative Commons licenses and users must assess their use case. This caveat and
the MIT declaration do not establish commercial rights for Lyria output.
Rights remain governed separately; see the measured
[production closeout](../MUSIC_TIMING_QA_PRODUCTION_CLOSEOUT.md).

The real CPU validation on 2026-09-27 used Python 3.11.9, WhisperX 3.8.6, faster-whisper 1.2.1, CTranslate2 4.8.2, torch/torchaudio 2.8.0, torchvision 0.23.0, transformers 4.57.6, huggingface-hub 0.36.2, NLTK 3.10.3, librosa 0.11.0 and numpy 2.4.6. The original lock required no dependency changes. FFmpeg 8.1 decoded the MP3. Pyannote's optional TorchCodec decoder warns about unavailable DLLs; WhisperX uses FFmpeg and passes in-memory waveforms, and actual CPU inference/alignment succeeded without that decoder.

The authoritative database already contained incomplete versions 1 and 2. With operator authorization, the successful offline run was persisted as version 3. It recognized 54 words against 36 expected words (30 matches, 0 substitutions, 24 insertions, 6 deletions; WER/coverage both 0.8333333333333334). Canonical alignment produced 36 words and 7 lines, minimum score 0.294. Timing admission correctly rejected WER, coverage and score; downbeats also remain missing. QA failed and the 58.01795918367347-second duration still exceeds 45 seconds. Rights remain unknown and approval pending. No generation or provider-resume calls occurred. CI mocks model loaders and downloads no models; the minimal development install also passes the regression suite.


### Windows CUDA verifier

Controlled preparation accepts `small.en`, `medium.en`, and `large-v3` only. The
`large-v3` cache requires its JSON vocabulary and preprocessor configuration;
English-only snapshots retain their text vocabulary checks. Inventory reuse also
requires the exact current asset paths, so switching cached models cannot reuse
another model's hashes.

`analysis-doctor --device cuda` reports the PyTorch version/CUDA build, cuDNN,
GPU count/name/VRAM, and CTranslate2 device count and supported compute types.
These device queries do not prove model inference or successful runtime DLL
loading. CUDA readiness requires the requested FP16 support from both stacks;
perform a cache-only model/inference preflight before allocating a new immutable
analysis version. CPU diagnostics and CI do not require NVIDIA hardware.

See [the large-v3 CUDA verification report](LARGE_V3_CUDA_ASR_FINAL_VERIFICATION.md)
for the isolated Windows environment, runtime DLL selection, and retained-artifact
experiment. Do not synchronize the normal project environment onto CUDA wheels.
