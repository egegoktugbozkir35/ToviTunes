# medium.en ASR cross-check

Classification: **LIKELY_GENERATION_LYRIC_DEVIATION**.

Both independent ASR runs materially agree on repeated chorus material and omission of the canonical second line. The larger model changes only one lexical token (`colour` to `color`) plus punctuation. It does not restore canonical structure. This is evidence of likely generation deviation, rather than small-model transcription error; agreement between related Whisper models is not proof from a human listening review.

## Identity and scope

- Repository: `egegoktugbozkir35/ToviTunes`; branch: `codex/medium-en-asr-crosscheck`.
- Starting `origin/main`: `6a7ba1857d9c861da8632fd0098a391f9303fd32` (verified).
- Blind ID: `mb_49286b900bcb4dd69f84fd857bbae7ef`; request ID: `451ba192-da15-46d3-b5fd-39712190c838`.
- Original retained MP3: `451ba192-da15-46d3-b5fd-39712190c838.mp3`.
- SHA-256 before and after: `613d42c4cbbb133366cde4378585197a82246b3fe7106eb69996466a7e0dd9c0`.
- Version 3 row, including exact serialized analysis JSON and creation timestamp, verified unchanged. Its analysis JSON SHA-256 is `b60790db2cb4fe840dd0e2d4d077b3a6f4491176621e2a8a7e6651555b6e7d25`.
- Version 4 is newly persisted immutable evidence; analysis JSON SHA-256: `5d1affe855ac5c3f8a7b4e91d9bdfa750f3ae938fec128c7b8a2c300b7104cdc`.

## Exact transcripts

### small.en — version 3

```text
Red, red, look ahead A red, apple, round and bright A red, ball rolls into sight Red, red, what do you see? Red is a colour, sing with me Red, what do you see? Red is a color, sing with me. Red, red, what do you see? Red is a color, sing with me.
```

### medium.en — version 4

```text
Red, red, look ahead A red apple, round and bright A red ball rolls into sight Red, red, what do you see? Red is a color, sing with me Red, what do you see? Red is a color, sing with me. Red, red, what do you see? Red is a color, sing with me.
```

### Canonical lyrics

```text
Red, red, look ahead!
Red is a color, yes, red!
A red apple, round and bright.
A red ball rolls into sight.
Red, red, what do you see?
Red is a color, sing with me!
Red!
```

## Side-by-side metrics

| Measurement | small.en / v3 | medium.en / v4 |
|---|---|---|
| Canonical words | 36 | 36 |
| Recognized words | 54 | 54 |
| Matches | 30 | 30 |
| Substitutions | 0 | 0 |
| Insertions | 24 | 24 |
| Deletions | 6 | 6 |
| WER | 0.8333333333333334 | 0.8333333333333334 |
| Coverage | 0.8333333333333334 | 0.8333333333333334 |
| Independent aligned words | 54 | 54 |
| Mean independent CTC score | 0.6870555555555555 | 0.6912037037037037 |
| Canonical aligned words / lines | 36 / 7 | 36 / 7 |
| Canonical minimum CTC score | 0.294 | 0.294 |
| Transcription / canonical alignment status | complete / complete | complete / complete |
| Duration (seconds) | 58.01795918367347 | 58.01795918367347 |
| Downbeats | 0 | 0 |
| Exported timing words / lines / sections | 0 / 0 / 0 | 0 / 0 / 0 |
| QA policy | fail | fail |
| Timing policy | fail | fail |
| Rights | unknown | unknown |
| Music approval | pending | pending |

WER delta (v4 minus v3): **0.0**. Coverage delta: **0.0** (0 percentage points). Mean independent CTC score delta: **0.004148148148148123**. Canonical minimum score delta: **0.0**.

Required teaching phrases are present in both: `red`, `color`, `red apple`, `red ball`, and `red is a color`. Phrase presence does not establish correct order or canonical completeness.

## Sequence and repetition comparison

Both runs recognize this order:

1. `Red, red, look ahead`
2. `A red apple, round and bright`
3. `A red ball rolls into sight`
4. `Red, red, what do you see?`
5. `Red is a color/colour, sing with me`
6. `Red, what do you see?`
7. `Red is a color, sing with me`
8. `Red, red, what do you see?`
9. `Red is a color, sing with me`

| Phrase / structure | small.en / v3 | medium.en / v4 | Canonical expectation |
|---|---|---|---|
| `Red, red, what do you see?` | 2 | 2 | 1 |
| Shortened `Red, what do you see?` | 1 | 1 | 0 |
| `Red is a color, sing with me!` (punctuation ignored) | 2, plus 1 with `colour` | 3 | 1 |
| `Red is a color, yes, red!` | absent | absent | 1, immediately after opening |
| Standalone final `Red!` | not recognized as a separate ending | not recognized as a separate ending | 1 |

**A:** Yes, medium.en also hears three question/answer chorus cycles: two full questions and one shortened question. All three answer lines use `color`.

**B:** No, medium.en does not hear the apparently missing canonical second line. Both move directly from the opening into the apple line, omit `yes`, and retain six deletions in canonical comparison.

**C:** Yes, both agree on the broad structure and exact token order except the spelling at recognized token 26 (`colour` → `color`). The extra cycles account for 24 insertions. The absent separate ending is a sequence observation; the edit-distance algorithm can match its final expected `red` inside the last repeated chorus, so aggregate deletions alone cannot describe the ending.

CTC scores measure alignment fit, not calibrated ASR confidence; recognition confidence remains unavailable. Canonical forced alignment supplies the expected text, so 36/36 words and 7/7 lines are not independent evidence that all canonical lyrics were sung. The identical minimum score of 0.294 is below the unchanged 0.5 timing threshold. Neither model received canonical lyrics as an ASR prompt.

## Provisioning and offline execution

Authoritative configuration:
`C:\Users\Victus\Documents\Codex\2026-09-25\files-pasted-by-the-user-tovitunes-2\work\ToviTunes-live\config.example.yaml`.

- Device: CPU; ASR compute type: int8; Python 3.11.9.
- WhisperX `3.8.6`, faster-whisper `1.2.1`, CTranslate2 `4.8.2`.
- Explicitly provisioned ASR: `medium.en`, action: **downloaded**.
- Resolved model revision: `a29b04bd15381511a9af671baec01072039215e3`.
- Cache snapshot: `C:\Users\Victus\Documents\Codex\2026-09-25\files-pasted-by-the-user-tovitunes-2\work\ToviTunes-live\data\music-benchmark\.analysis-models\asr\models--Systran--faster-whisper-medium.en\snapshots\a29b04bd15381511a9af671baec01072039215e3`.
- Alignment, English punkt_tab tokenizer, and bundled VAD: **reused**. No other Whisper model downloaded.
- Preparation validated cache-only loaders successfully. Doctor: ASR cached, alignment cached, tokenizer cached, VAD cached, FFmpeg found, no preflight failures, `offline_ready=true`.
- FFmpeg: `ffmpeg version 8.1-full_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers`.
- Installed faster-whisper already maps `medium.en` to `Systran/faster-whisper-medium.en`; only the two preparation allowlists needed expansion.

Commands ran from the new branch with `PYTHONPATH` set to its `src`, and `UV_PROJECT_ENVIRONMENT` pointing to the existing validated Python 3.11 ASR environment. `uv run --no-sync` preserved that installed runtime. The environment is under the previous cross-check workspace, as recorded in `medium-en-doctor.json`; the authoritative config, audio, cache and database stayed in ToviTunes-live.

```powershell
uv run --no-sync python -m tovitunes.cli --config $config music-benchmark analysis-models prepare --asr-model medium.en --device cpu --allow-model-download
uv run --no-sync python -m tovitunes.cli --config $config music-benchmark analysis-doctor --asr-model medium.en --device cpu
uv run --no-sync python -m tovitunes.cli --config $config music-benchmark analyze-audio --blind-id mb_49286b900bcb4dd69f84fd857bbae7ef --analysis-version 4 --device cpu --asr-model medium.en
uv run --no-sync python -m tovitunes.cli --config $config music-benchmark policy-evaluate --type timing --blind-id mb_49286b900bcb4dd69f84fd857bbae7ef --version 4
```

Here `$config` is the exact authoritative path above. Only provisioning had download permission. Version 4 records `allow_model_download=false`; inference used offline flags, local-only assets and the existing outbound socket guard. Optional TorchCodec DLL warnings did not prevent FFmpeg decoding, ASR or alignment; transcription and alignment completed.

## QA, timing and preserved safety

Version 4 normal analysis QA ran automatically: **fail**; evaluation `df2c4f35-744d-4f60-a2bf-821a5cc7144d`. Explicit timing evaluation: **fail**; evaluation `3800e19b-6e95-4d5a-b2ad-09b1ad7c055f`.

Duration is **58.01795918367347 seconds (~58.018)**, exceeding the 45-second limit by 13.01795918367347 seconds. The duration failure remains. Lyric adherence, teaching intelligibility, educational correctness, preschool safety and production fit also fail under existing policy; beat usability passes and artifact-free evidence remains unknown.

Timing retains 100 detected beats but no downbeats. Canonical alignment is retained as analysis evidence; its words/lines are withheld from exported timing because WER, coverage and minimum alignment score fail existing reliability gates. Timing has zero words, lines and sections and remains pending approval. No downbeats fabricated.

Rights remain **unknown**; music approval remains **pending**. Requests, receipts, outputs and decisions were compared with their pre-run rows and are unchanged; all pre-existing timing rows were also verified unchanged.

- Generation POSTs = **0**.
- Provider-resume GETs = **0**.
- Songs generated = **0**.
- Audio modification, trimming and transcoding = **0**. Normal decoding for analysis only; original MP3 SHA unchanged.
- Threshold weakening, rights approval and music approval = **0**.

These action counts describe this cross-check session, whose only external operation was authorized model provisioning.

## Validation and change scope

Before provisioning: `uv run --no-sync pytest -q` — **251 passed**; `uv run --no-sync ruff check .` — passed; `uv run --no-sync mypy src` — passed, 41 source files; `git diff --check` — passed. Tovi `character-pack validate-lock` — **valid, 48 artifacts**.

CI installs only the `dev` extra. Runtime preparation tests use mocked loaders/downloaders and forbidden outbound connections; audio analysis tests similarly mock ASR and block network. The added tests cover both approved models, download/reuse behavior, cache-only loading, CLI forwarding and rejection of unapproved models. CI does not obtain ASR assets.

Commit scope: two narrow allowlist changes, offline tests and this report. No runtime/cache/database/audio artifacts are committed. The PR is opened for review and must not be merged automatically.

Local supporting evidence: `analysis-version3.json`, `analysis-version4.json`, `medium-en-preparation.json`, `medium-en-doctor.json`, `qa-version4.json`, `timing-version4.json`, and `integrity-check.json` alongside this report in the task outputs.
