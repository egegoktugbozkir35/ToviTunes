# Canonical timing admission closeout

**MUSIC_TECHNICAL_PIPELINE_READY_FOR_STORYBOARD**

Candidate 2 immutable analysis/timing version **3** passes analysis-derived QA and
the existing timing policy. Music engineering is frozen for this candidate.
Next implementation phase: **TimedStoryboard → Tovi animation → compositor/render**.
Rights remain **unknown** and approval remains **pending**, separate Production V1
release blockers.

Base: latest fetched `origin/main`,
`d9f4d3e71d7cab637f801f9f2b95d23a449a322f`, containing merged PR #24.
Branch: `codex/fix-canonical-timing-admission`. No automatic merge.

## Existing blocker

Version 2 had complete canonical timestamps for 36/36 words and 7/7 lines, but
the single canonical `you` at 23.195–23.395 seconds scored 0.421. The old all-word
absolute score veto withheld every production word/line/section and lyric edge.
Independent ASR had WER 0.027777777777777776, coverage 0.9722222222222222 and
mean word alignment score 0.8061714285714285. Duration was 38.164897959183676
seconds, under the frozen 45-second maximum.

## WhisperX score semantics

Inspected the actual cached production environment before changing policy:
WhisperX **3.8.6**, Faster-Whisper **1.2.1**, CTranslate2 **4.8.2**,
Torch/torchaudio **2.8.0+cu128**, Beat This **1.1.0**, Python **3.11.9**.
Installed `whisperx/alignment.py` SHA-256:
`cc3607dcc25e592ae218f74a6ca644d6ce1c77d9cee4672f01c751da8feb9aa7`.

The installed code applies log-softmax to CTC emissions (lines 258–265),
exponentiates the selected token/blank emission along the forced path (477),
averages repeated-character path scores (514), then computes a word's arithmetic
mean over non-space character scores, rounded to three decimals (351).
[Released WhisperX source](https://github.com/m-bain/whisperX/blob/v3.8.6/whisperx/alignment.py#L351)
confirms this calculation. There is no universal per-word >=0.5 admission
requirement in that implementation. The inspected source does not establish
these scores as calibrated word-recognition probabilities.

Scores remain useful diagnostic alignment evidence. Canonical forced alignment
receives canonical text, so it cannot independently prove that text was sung.
All original scores remain persisted; no recognition confidence was fabricated.

## Why per-word absolute threshold was brittle

The low score is isolated: 35 of 36 canonical words exceed 0.5 and the mean is
0.7956111111111112. Treating one character-average score as a universal
recognition-probability veto discarded complete timing despite strong independent
recognition. The fix retains the same numeric 0.5 as minimum aggregate quality
for both sources, without dropping diagnostic scores or allowing poor recognition
to be rescued by a forced canonical alignment.

## Candidate-2 full canonical score distribution

Read directly from the authoritative SQLite DB using URI `mode=ro` and
`PRAGMA query_only=ON`, before modifying admission logic. Version 2 was never
rewritten. Its persisted analysis JSON SHA-256 remains
`392a4132ee1235ce4bb6fb7df3255a93bca7a617c39f0acab33f988dc998f2ac`.

| # | Text | Start (s) | End (s) | CTC alignment score |
| --- | --- | --- | --- | --- |
| 1 | `Red,` | 2.462 | 2.922 | 0.872 |
| 2 | `red,` | 3.022 | 3.462 | 0.887 |
| 3 | `look` | 3.562 | 3.822 | 0.840 |
| 4 | `ahead!` | 3.943 | 6.925 | 0.755 |
| 5 | `Red` | 6.925 | 7.485 | 0.788 |
| 6 | `is` | 7.605 | 7.725 | 0.778 |
| 7 | `a` | 7.805 | 7.905 | 0.696 |
| 8 | `color,` | 7.985 | 8.686 | 0.747 |
| 9 | `yes,` | 9.106 | 9.486 | 0.918 |
| 10 | `red!` | 9.666 | 11.147 | 0.855 |
| 11 | `A` | 11.147 | 11.227 | 0.945 |
| 12 | `red` | 11.347 | 12.308 | 0.806 |
| 13 | `apple,` | 12.568 | 13.289 | 0.848 |
| 14 | `round` | 13.549 | 14.089 | 0.921 |
| 15 | `and` | 14.349 | 14.549 | 0.523 |
| 16 | `bright.` | 14.650 | 15.570 | 0.678 |
| 17 | `A` | 15.570 | 15.630 | 0.943 |
| 18 | `red` | 15.770 | 16.211 | 0.791 |
| 19 | `ball` | 16.311 | 16.811 | 0.868 |
| 20 | `rolls` | 16.891 | 17.652 | 0.785 |
| 21 | `into` | 18.172 | 18.752 | 0.795 |
| 22 | `sight.` | 18.812 | 20.193 | 0.792 |
| 23 | `Red,` | 20.193 | 20.754 | 0.830 |
| 24 | `red,` | 21.314 | 21.854 | 0.841 |
| 25 | `what` | 22.395 | 22.655 | 0.784 |
| 26 | `do` | 22.695 | 23.175 | 0.790 |
| 27 | `you` | 23.195 | 23.395 | 0.421 |
| 28 | `see?` | 23.495 | 24.636 | 0.836 |
| 29 | `Red` | 24.636 | 25.216 | 0.827 |
| 30 | `is` | 25.337 | 25.437 | 0.905 |
| 31 | `a` | 25.537 | 25.637 | 0.713 |
| 32 | `color,` | 25.717 | 26.377 | 0.865 |
| 33 | `sing` | 26.797 | 27.218 | 0.831 |
| 34 | `with` | 27.418 | 27.818 | 0.694 |
| 35 | `me!` | 27.958 | 29.079 | 0.873 |
| 36 | `Red!` | 29.079 | 29.319 | 0.601 |

| Statistic | Exact result |
| --- | --- |
| Count | 36 |
| Minimum | 0.421 |
| Maximum | 0.945 |
| Arithmetic mean | 0.7956111111111112 |
| Median | 0.8165 |
| Count below 0.5 | 1 |
| Percentage below 0.5 | 2.7777777777777777% |

Version 3 reproduces every canonical word, timestamp and score exactly.

## Independent evidence for low-score words

Only canonical word 27 (`you`) is below 0.5. Persisted edit operation 27 is
`{"kind":"match","expected":"you","recognized":"you"}`.
Operations 25–29 match `what`, `do`, `you`, `see`, `red` in sequence.
Independent recognized word 27 is `you`, 23.179–23.379 seconds, CTC alignment
score 0.441. Its surrounding independent words are `what` (22.398–22.658,
0.850), `do` (22.698–23.159, 0.708), `see?` (23.479–24.059, 0.692) and
`Red` (24.619–25.219, 0.855). The independently decoded transcript contains
`Red, red, what do you see?`; canonical lyrics were never supplied as an ASR prompt.

The independent transcript has 35/36 matches, zero substitutions, zero insertions
and one deletion. The deletion is final standalone `Red!`; its canonical timing
remains measured at 29.079–29.319 seconds, score 0.601. Existing WER/coverage
policies handle that disagreement. No requirement or exception was added for
`you`, `Red!`, short words or any other word.

## Old admission rule

```python
all(word.score is not None and word.score >= 0.5
    for word in alignment.canonical_words)
```

The old rule also checked alignment/comparison completion, WER <=0.25 and
coverage >=0.85, but did not explicitly require independent transcription
completion and its mean alignment quality.

## New admission rule

All of the following are required:

- Independent transcription and lyric comparison complete; WER <=0.25;
  coverage >=0.85; existing independent mean word CTC score present and >=0.5.
- Canonical alignment complete; no missing timestamps/words; every canonical
  score present; arithmetic mean of all canonical word scores >=0.5.
- Canonical count and normalized text match expected canonical identity;
  comparison belongs to the same canonical and recognized transcripts;
  every lyric line is present and spans its corresponding measured word group;
  word/line timestamps are finite, ordered and bounded by actual duration.

`build_timing` receives the `TranscriptionEvidence` already derived in the same
authoritative analysis run. No duplicate ASR pipeline or second ASR computation
was introduced. Production words, lines, sections and positive pre/post-lyric
intervals use measured timestamps unchanged. No manual timing or interpolation.

No persistence model or migration changed. Immutable analysis retains the raw
evidence; immutable QA policy evidence now records rule `aggregate_two_source_v1`,
independent mean, canonical mean/minimum/minimum-score word, low-score count,
missing-score/timestamp counts, WER, coverage, individual checks, eligibility
under the current rule and the actual persisted admission decision. Historical
deserialization never reapplies admission or populates historical withheld timing.

## Thresholds unchanged

The `AnalysisThresholds` class, canonical YAML, QA thresholds, timing policy,
duration limit and beat requirements are unchanged:

| Threshold | Value |
| --- | --- |
| minimum_timing_coverage | 0.85 |
| maximum_timing_wer | 0.25 |
| maximum_lyric_wer | 0.15 |
| minimum_lyric_coverage | 0.9 |
| minimum_alignment_score | 0.5 |
| maximum_intelligibility_wer | 0.2 |
| maximum_educational_wer | 0.25 |
| maximum_insertion_ratio | 0.1 |
| minimum_beats | 12 |
| maximum_beat_interval_cv | 0.3 |
| maximum_silence_ratio | 0.2 |
| maximum_clipping_ratio | 0.001 |
| maximum_dropout_seconds | 2.0 |
| maximum_edge_silence_seconds | 2.0 |
| allowed BPM range | 100–124 |
| maximum_duration_seconds | 45 |

## Candidate-2 immutable version-3 analysis

Verified analysis/timing versions were `[1, 2]` immediately before the run;
next unused version was **3**, now `[1, 2, 3]`.

- Request: `152f47fa-54f6-4bb3-ad67-8dc5467380d5`.
- Blind ID: `mb_3f657849e3d04060a0107940b098fb60`.
- Persisted analysis JSON SHA-256: `1b381cf5913549fd0644a41399ed2780d0e61090e22ea2b893bbffbfbfc800a5`.
- Independent `large-v3`, CUDA, FP16, English, batch size 4, cached
  Faster-Whisper revision `edaa852ec7e145841d8ffdb056a99866b5f0a478`.
- Cached WAV2VEC2 alignment SHA:
  `488fd4f16de84438ffc945334278c1b9fb9b7159a806c1080b16111a958c945d`.
- Cached Beat This `final0`, CUDA FP16, DBN=false, checkpoint SHA:
  `8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331`.
- Complete workload enclosed in `model_environment(cache, False)` with
  socket connect/connect_ex/create_connection blocked and HF/Transformers offline.
  No download permission and no provisioning command.
- Version 2 and 3 transcription, lyric comparison, canonical alignment, technical
  measurements, beat sequence and downbeat sequence are identical.

Historical row-digest multisets prove every preexisting row remains byte-for-byte
in every table. All eight stored analyses deserialize, including all seven
preexisting analyses. Only these rows were appended:

| Table | Before | After | Added |
| --- | --- | --- | --- |
| `music_audio_analysis` | 7 | 8 | +1 |
| `music_policy_evaluations` | 15 | 17 | +2 |
| `music_timing` | 7 | 8 | +1 |
| `music_timing_decisions` | 0 | 1 | +1 |

The timing decision is the existing policy's automatic machine decision after
its real measured checks passed. It is not a human/manual timing approval and
does not change candidate/output approval.

## QA result

Overall **pass**, music QA v2. Evaluation `12da2815-ef81-43e5-b555-bbc146343ae5`.
Decode/receipt verification and actual-duration scope checks both pass.

| Category | Result |
| --- | --- |
| artifact free | pass |
| beat usable | pass |
| educational correctness | pass |
| lyric adherence | pass |
| preschool safety | pass |
| production fit | pass |
| teaching intelligibility | pass |

Admission evidence: independent mean 0.8061714285714285; canonical mean
0.7956111111111112; minimum 0.421 (`you`); low-score count 1; missing-score count
0; missing timestamps 0; WER 0.027777777777777776; coverage 0.9722222222222222;
eligible=true; admitted=true. All admission checks pass.

Technical observations: clipping ratio 0; invalid PCM 0; silence ratio
0.013623264343725438; longest dropout 0.25 seconds; beginning/ending silence
0.09/0.25 seconds. Artifact-free remains scoped to configured deterministic
digital defect checks, not a claim of perceptual perfection.

## Timing result

Overall **pass**, existing music timing v1. Evaluation `908ef147-9b07-4593-bc14-4ed864766f18`.
All eight policy checks pass: SHA match, duration match, beats, downbeats,
words, lines, sections and ordering. No manual approval.

| Measured/populated evidence | Result |
| --- | --- |
| Actual duration | 38.164897959183676 seconds |
| Beats / downbeats | 61 / 16 |
| Estimated BPM / interval CV | 108.04321728691477 / 0.02117069878577254 |
| Canonical words / lyric lines / sections | 36 / 7 / 4 |
| First / last beat | 0.22 / 33.54 seconds |
| First / last downbeat | 0.22 / 33.54 seconds |
| Measured pre-lyric interval (intro) | 0–2.462 seconds |
| Measured post-lyric interval (outro) | 29.319–38.164897959183676 seconds |

Intervals are measured edges, not claims that those regions are purely instrumental.
No beat was invented to fill the audio tail. Phonemes remain absent.

## Audio immutability

Candidate-2 MP3 SHA-256 remains
`06820bff29d0127ae3b25e53c73ba96b874732916acee5b4800e688c1d73e991`.
Full retained-MP3 path/hash mapping is unchanged before/after. Candidate 1
`mb_49286b900bcb4dd69f84fd857bbae7ef`, including all five historical versions,
is unchanged. Candidate-2 versions 1 and 2 are unchanged. No audio trim,
rewrite, transcode, normalization, regeneration or candidate 3.

## Provider-call audit

- Lyria POSTs = **0**.
- Provider-resume GETs = **0**.
- Google generation calls = **0**.
- Songs generated = **0**; candidate 3 = **not generated**.

Generation, generation-with-identity, benchmark run and provider-resume entry
points were patched to raise during authoritative execution; attempts = 0.
Outbound sockets were blocked for doctor, actual inference and both policy calls.
Unchanged full-table request/receipt/output digests corroborate zero provider work.
Test-only local fixtures are offline and never call production providers.

## Rights/approval state

Persisted candidate/output **rights = unknown**, **approval = pending**.
No rights evaluation or output approval workflow was run. All music outputs,
rights/approval decisions, requests, receipts and reviews are unchanged.

## Validation

- `uv run pytest -q`: **328 passed** (base suite 311; 17 additional cases).
- `uv run ruff check .`: passed.
- `uv run mypy src`: passed, 42 source files.
- `git diff --check`: passed.
- Character-pack `validate-lock`: passed, **48 artifacts**.

Regressions cover isolated 0.421 at multiple generic positions in 36 words,
canonical mean <0.5, high independent WER, low coverage, low/missing independent
mean, incomplete transcription/alignment, missing score/timestamp/line,
word/line identity mismatch, unordered/out-of-bounds timestamps, measured zero
length edges and unchanged historical parsing/rows. Numeric thresholds were
not lowered. CI remains CPU-capable, GPU-independent, provider-free and uses
mocked ML inference with no model downloads; CI/dependencies are unchanged.

Supporting local outputs retain the full version-2 diagnostic, version-3
analysis, both evaluations, offline doctor, provider-call audit and all-table
before/after integrity evidence. No DB, MP3, model cache or environment is committed.

## Final classification

**MUSIC_TECHNICAL_PIPELINE_READY_FOR_STORYBOARD**

No unresolved technical audio blocker remains under the unchanged numeric
policies. Freeze candidate-2 music engineering and continue implementation with
**TimedStoryboard → Tovi animation → compositor/render**. Rights and approval
remain separate release gates; this classification is not publication approval.
