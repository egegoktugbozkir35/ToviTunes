# Music timing and QA production closeout

**MUSIC_TECHNICAL_PIPELINE_BLOCKED**

Candidate 2's canonical `you` at 23.195–23.395 seconds scores **0.421**, below
the unchanged **0.5** alignment admission threshold. The implementation gates
are now technically satisfiable, but this measured candidate cannot yet supply
admitted canonical storyboard timing. No regeneration or alignment exception
was performed.

## Starting state

- Latest fetched `origin/main` and clean starting HEAD:
  `97525a867992a41911a9516dc03bc28f3df263ab`.
- PR #23 was verified merged; its merge commit equals this base.
- Branch: `codex/music-timing-qa-production-closeout`.
- Baseline: 285 tests passed; Ruff, mypy (41 source files), `git diff --check`
  and Tovi character lock (48 artifacts) passed.
- Authoritative configuration remains the retained `ToviTunes-live` checkout's
  `config.example.yaml` from the September 25 benchmark task. Its SQLite DB
  and model cache were inspected read-only before changes, with URI `mode=ro`
  and `PRAGMA query_only=ON`. All-table digests/counts and audio hashes were
  captured before authoritative persistence.

## Candidate-2 identity

Identity was resolved from the successful live DB request with provider `google`,
model `lyria-3-pro-preview`, attempt 2 and persisted translated-request contract
`lyria_exact_lyrics_v2`.

| Field | Measured/persisted value |
| --- | --- |
| Request ID | `152f47fa-54f6-4bb3-ad67-8dc5467380d5` |
| Blind ID | `mb_3f657849e3d04060a0107940b098fb60` |
| Audio SHA-256 | `06820bff29d0127ae3b25e53c73ba96b874732916acee5b4800e688c1d73e991` |
| Duration | 38.164897959183676 seconds |
| Maximum | 45 seconds |
| Previous analysis/timing versions | 1 / 1 |
| New analysis/timing versions | 2 / 2, previously absent |
| Previous QA/timing | fail / fail |
| New analysis JSON SHA-256 | `392a4132ee1235ce4bb6fb7df3255a93bca7a617c39f0acab33f988dc998f2ac` |

Candidate 1 remains `mb_49286b900bcb4dd69f84fd857bbae7ef`, with all five
historical analysis/timing versions untouched.

## Existing blockers

Current base source confirmed that clean deterministic technical evidence gave
`artifact_free=unknown`, `build_timing` always emitted empty downbeats, and
production fit required intro/outro that analysis never produced. Those policy
implementation blockers are closed without removing gates or changing numeric
thresholds. A focused fixture test now passes both full analysis QA and timing
with valid measured evidence; missing evidence still cannot pass.

The previous candidate-2 alignment already had minimum score 0.421. That
candidate-specific evidence blocker is separate from the implementation issues.

## Reused external component

Released `beat-this==1.1.0`, public `beat_this.inference.Audio2Beats`, model
`final0`. Verified decoded stereo float32 PCM at 44100 Hz is supplied directly.
`dbn=False`, CUDA FP16; CPU diagnosis supported. No neural network was copied or
reimplemented, no madmom is used, no Torch Hub repository code is executed,
and no extra beat tracker or ASR model was added.

Installed package source confirmed local-file-first loading and its automatic
URL fallback on a missing file; runtime prechecks the path/hash and explicitly
disables that fallback. Installed `Audio2Beats` and minimal postprocessor
confirmed `(beats, downbeats)` ordering. A cache-only installed postprocessor
probe returned distinguishable arrays `[0.2, 0.8, 1.4, 2.0]` and `[0.8, 2.0]`.
Adapter tests independently exercise distinguishable outputs.

## Dependency/license provenance

The new `audio-timing` extra is optional alongside existing `audio-analysis`
and `audio-asr`. The lock adds Beat This and rotary-embedding-torch 0.9.1; it
retains the existing package versions. Production package freezes prove only
these two packages were added. Torch/torchaudio remain 2.8.0+cu128, WhisperX
3.8.6, Faster-Whisper 1.2.1 and CTranslate2 4.8.2, Python 3.11.9.

Sources: [upstream v1.1.0](https://github.com/CPJKU/beat_this/tree/v1.1.0),
[released package](https://pypi.org/project/beat-this/1.1.0/).
Upstream identifies code and published model weights as MIT licensed. The
installed wheel's license/attribution is retained in
[docs/BEAT_THIS_LICENSE.txt](docs/BEAT_THIS_LICENSE.txt).
Upstream warns that some training files are fully copyrighted or under limited
Creative Commons licenses and users must assess their use case. This does not
confirm commercial rights for ToviTunes or Lyria output.

## Beat This model provisioning

The authorized `analysis-models prepare-timing --device cuda
--allow-model-download` was run twice in the existing production environment.
It provisions only the Beat This checkpoint; the ASR inventory is preserved.

| Evidence | Value |
| --- | --- |
| Package / logical model | Beat This 1.1.0 / final0 |
| First preparation | downloaded; offline load validated |
| Second preparation | reused; offline load validated |
| Size | 81,058,141 bytes |
| SHA-256 | `8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331` |
| Cache suffix | `.analysis-models/torch/hub/checkpoints/beat_this-final0.ckpt` |
| Inventory | `.analysis-models/timing-inventory.json` |
| Source | `https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt` |
| Device / precision / DBN | CUDA / FP16 / false |

The installed loader accepts an absolute local checkpoint path. Runtime never
passes the short model name. Missing, changed, untracked or out-of-cache assets
fail closed, and preparation cannot silently replace inventoried content.

## Offline proof

Doctor reports ASR offline readiness true and timing offline readiness/model-load
readiness true. The selected checkpoint hash matches the preparation inventory.
The authoritative CLI invocation was:

```text
--config <AUTHORITATIVE_CONFIG> music-benchmark analyze-audio --blind-id mb_3f657849e3d04060a0107940b098fb60 --analysis-version 2 --device cuda --asr-model large-v3
```

Neither ASR nor timing download permission was supplied. The complete CLI
workload ran inside `model_environment(..., False)`, blocking `socket.connect`,
`connect_ex` and `create_connection`, with HF/Transformers offline flags.
Beat This also guards its own loader/inference and blocks upstream checkpoint
download fallback. Generation, generation-with-identity, benchmark run and
provider-resume entry points were patched to raise if invoked. The successful
run reported `reused=false`, complete rhythm and transcription. No assets were
downloaded during authoritative analysis. Model/device provenance is retained
with source audio SHA and checkpoint SHA.

WhisperX's optional TorchCodec DLL warning remains a diagnostic of its unused
built-in decoder. Actual FFmpeg/in-memory WhisperX inference and alignment
completed on the unchanged validated stack.

## ASR result

WhisperX → Faster-Whisper/CTranslate2, independent `large-v3`, CUDA FP16,
English, batch size 4, cache-only; canonical lyrics were not supplied as an
ASR prompt.

```text
Red, red, look ahead Red is a color, yes red A red apple, round and bright A red ball rolls into sight Red, red, what do you see? Red is a color, sing with me
```

| Metric | Result |
| --- | --- |
| Recognized / canonical word count | 35 / 36 |
| WER | 0.027777777777777776 |
| Coverage | 0.9722222222222222 |
| Insertions / deletions / substitutions | 0 / 1 / 0 |
| Required phrases | red, color, red apple, red ball, red is a color: all present |
| Mean independent word CTC alignment score | 0.8061714285714285 |
| Canonical aligned words / lines | 36 / 7 |
| Missing canonical timestamps | none |
| Minimum canonical score | 0.421 (`you`, 23.195–23.395 seconds) |

The restored canonical second line remains independently recognized. No extra
sung words or repeated lyric lines appear in the transcript. CTC scores are
alignment evidence, not calibrated recognition confidence.

## final Red evidence

Independent ASR still does not recognize the standalone final `Red!`.
Canonical forced alignment observes `Red!` at **29.079–29.319 seconds**, score
**0.601**. Forced alignment has canonical text as input and therefore does not
independently prove the word was sung. Its score exceeds 0.5, but every canonical
word must pass; `you` at 0.421 blocks admission. No word-specific logic,
interpolated endpoint or manually inserted timestamp was introduced.

## Beat/downbeat result

| Metric | Result |
| --- | --- |
| Beat count | 61 |
| Downbeat count | 16 |
| Estimated BPM | 108.04321728691477 |
| Mean interval | 0.5553333333333333 seconds |
| Population interval std | 0.011756794725699017 seconds |
| Interval CV | 0.02117069878577254 |
| First / last beat | 0.22 / 33.54 seconds |
| First / last downbeat | 0.22 / 33.54 seconds |
| Device | cuda, FP16, DBN=false |

These are real Beat This outputs. Beats and downbeats share one measured clock;
BPM and interval statistics use that detector's beat sequence. Positions are
finite, increasing, bounded by actual duration; downbeats belong to its beat grid.
No later beats are invented to fill the tail of the audio.

## Intro/outro derivation

Generic implementation derives a positive pre-lyric interval from the first
admitted canonical word and a positive post-lyric interval from the last admitted
word through actual audio duration. Zero-length regions remain `None`. These
are not claims of purely instrumental edges and do not copy Lyria schedules.

Candidate 2 admits **0 words, 0 lines, 0 sections**, because `you` fails the
score threshold. Therefore its intro and outro remain **None**. Unadmitted
alignment cannot supply production edges. Fixture tests verify measured edge
derivation and zero-length behavior without weakening admission.

## QA before/after

Music QA policy v2 still requires every required check to pass.

| Check | Before | After | Metrics, unchanged threshold and exact reason |
| --- | --- | --- | --- |
| Lyric adherence | pass | pass | WER 0.02778 <=0.15; coverage 0.97222 >=0.9; independent mean score 0.80617 >=0.5 |
| Educational correctness | pass | pass | All required teaching phrases present; no configured contradiction; WER 0.02778 <=0.25 |
| Teaching intelligibility | pass | pass | Mean CTC score 0.80617 >=0.5; coverage >=0.9; WER <=0.2; required teaching phrases present |
| Preschool safety | pass | pass | No configured forbidden words; insertion ratio 0 <=0.1; adequate recognition |
| Beat usable | pass | pass | 61 beats >=12; 108.0432 BPM in [100,124]; CV 0.02117 <=0.3 |
| Production fit | unknown | unknown | Duration <=45, technical checks clear and edges' silence <=2 seconds; canonical `you` score 0.421 <0.5 prevents admitted words/lines/sections and measured intro/outro |
| Artifact free | unknown | pass | Decode/source verified; 0 invalid PCM; clipping 0 <=0.001; silence ratio 0.013623 <=0.2; longest dropout 0.25 <=2 seconds |
| Overall | fail | fail | Production-fit prerequisite evidence is not admitted, so not every required check passes |

Beginning/ending silence is 0.09/0.25 seconds (both <=2). Peak amplitude is
0.99884033203125. `artifact_free` now means all configured deterministic digital
defect checks are clear, with source verification available. Detected defects
fail; unavailable verification remains unknown. No broad perceptual-perfection
or aesthetic claim is made.

QA evaluation: `b4b6aee6-3ca9-4874-8278-b8c565296c2e`.

## Timing before/after

Music timing policy v1 and all admission thresholds are unchanged:
coverage >=0.85, WER <=0.25, every canonical word score >=0.5.
Coverage/WER pass; the minimum alignment score does not.

| Timing check | Before | After |
| --- | --- | --- |
| Audio SHA / actual duration match | true / true | true / true |
| Beats present | true | true |
| Downbeats present | false | true |
| Ordered | true | true |
| Words / lines / sections present | false / false / false | false / false / false |
| Overall | fail | fail |

Timing evaluation: `2a1b6bec-fca8-4095-8302-f24ff184e4f4`. No manual timing
approval or timing decision was added.

## Historical immutability

Read-only post-run row-digest comparison proves every preexisting row is present
byte-for-byte in all tables. Only these append counts changed:

| Table | Before | After |
| --- | --- | --- |
| music_audio_analysis | 6 | 7 |
| music_timing | 6 | 7 |
| music_policy_evaluations | 13 | 15 |

Exactly one candidate-2 analysis and one timing row at version 2 were added, plus
one derived QA and one timing evaluation. Candidate-2 version 1 and all candidate-1
versions are unchanged and deserialize with the extended defaults. Both retained
MP3s rehash to their original SHA-256. Requests, receipts, outputs, reviews,
rights/approval/lyric/timing decisions and every other table are unchanged.

## Provider-call audit

- Lyria generation POSTs = **0**.
- Provider-resume GETs = **0**.
- Google generation calls = **0**.
- Songs generated = **0**.
- No candidate 3, regeneration, trim, rewrite, transcode or normalization.
- Candidate MP3 SHA unchanged, and candidate 1 untouched.

Fail-closed provider entry-point guards and the complete outbound socket guard
cover the real analysis/policy calls. Unchanged full-table request/receipt/output
digests support the audit. Only authorized Beat This dependency assets and the
final0 checkpoint were newly provisioned; ASR assets were used offline.

## Rights/approval state

Candidate-2 rights remain **unknown**, approval remains **pending**. No rights
confirmation, approval-policy execution or approval decision occurred. These
remain separate release gates.

## Validation

- `uv run pytest -q`: **311 passed** (baseline 285).
- `uv run ruff check .`: passed.
- `uv run mypy src`: passed, 42 source files.
- `git diff --check`: passed.
- Tovi character-lock validation: passed, 48 artifacts.
- Separate clean minimal install: CLI/config import succeeds, no Torch/Beat This.
- Full production environment: `uv pip check` passes; only Beat This and rotary
  embeddings added; actual offline CUDA inference/ASR/alignment completed.
- CI remains GPU-independent, with mocked inference and no model/network download.

Focused tests cover optional-dependency isolation, explicit permission and cache
reuse, hash mismatch/missing checkpoint/package, download fallback blocking,
tuple mapping, bounds/order/empty downbeats, DBN/FP16/local paths, measured edges,
zero-length edges, generic low-score rejection, clean/defective/unavailable
technical evidence, historical defaults, provider isolation, and a complete
passing QA/timing fixture. Existing policies and canonical YAML remain frozen.

Supporting local output files retain preparation inventories, doctor/probe,
candidate-2 before/after analysis, QA/timing evaluations, package freezes and
all-table integrity snapshots. No DB, MP3, checkpoint, environment or model
weights are committed.

## Final technical-readiness classification

**MUSIC_TECHNICAL_PIPELINE_BLOCKED**

The sole measured technical blocker is canonical `you` alignment score
**0.421 <0.5**, which prevents admitting the production lyric timing. The new
detector and scoped artifact gate work. Do not promote this candidate to the
provisional storyboard audio master or generate another song automatically.
