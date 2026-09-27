# TimedStoryboard V1 production handoff

## Starting main

Base SHA: `3d0bf0bd6572a5096bfb4c39819c8d753b623d49`, latest fetched `origin/main` containing merged [PR #25](https://github.com/egegoktugbozkir35/ToviTunes/pull/25).

Branch: `codex/production-audio-handoff-timed-storyboard`. Fresh main checkout; no old music branch. Music engineering and thresholds are unchanged. No automatic merge.

## Candidate-2 technical readiness

Persisted identity: provider `google`, model `lyria-3-pro-preview`, attempt **2**, prompt contract `lyria_exact_lyrics_v2`, blind ID `mb_3f657849e3d04060a0107940b098fb60`, local request `152f47fa-54f6-4bb3-ad67-8dc5467380d5`, provider request `phW5at2bI8eGstMP8K3U4QY`.

Loaded immutable analysis/timing version **3**, not prompt-requested timing. Readiness is `MUSIC_TECHNICAL_PIPELINE_READY_FOR_STORYBOARD`, established from persisted passing technical evidence:

- Music QA v2 pass: `12da2815-ef81-43e5-b555-bbc146343ae5`, bound to the exact audio SHA and persisted analysis JSON SHA.
- Music timing v1 pass: `908ef147-9b07-4593-bc14-4ed864766f18`, bound to the exact audio SHA and canonical version-3 timing JSON SHA.
- Source duration: **38.164897959183676 seconds**; 36 canonical words, 7 lines, 4 sections, 61 beats, 16 downbeats.
- Live original rights **unknown**, music-output approval **pending**. No QA/timing reevaluation or analysis was run.

Preflight uses a read-only SQLite snapshot and verifies retained bytes, receipt size/SHA/duration, succeeded request/provider identity, admitted timing equality, current policy passes and the pinned pilot identity before any production writes.

## Episode identity

Episode ID: `e9751591-906e-4404-88a2-31e7a41071d6`. External key: `colors-red-001`. Concept: `red`. Objective ID: `colors.red.identify`.

Curriculum objective: **Identify red in a clearly shown familiar object.**

- Brand revision: `tovitunes-1e34c1c7f5ed0fd7`.
- Curriculum revision: `colors-b7cda6b48c764750`.
- Pinned Tovi pack: `tovi-pack-v1-8f7e487b5ac5279b`.

Revisions come from the existing versioned catalog and `Episode.create(...)`; no revision ID is hard-coded. The external key is unique. A conflicting concept, curriculum, objective, brand or character-pack revision fails before mutation.

One explicit machine episode decision uses `canonical_curriculum_v1` to approve the pinned committed objective solely for technical production planning. No human prompt was added; later rejected or needs-review decisions are not overridden.

## Audio-master handoff

Audio master artifact: `ace1ea8d-6a31-47a2-bb2a-2a895ecd9604` (`kind=audio_master`, `slot=main`, episode owner).

Source handoff manifest artifact: `2aa71a24-8251-4176-a4c2-09e680af5fc5` (`kind=production_handoff`, `slot=main`). This small immutable JSON dependency retains provider, model, request identities, prompt contract, blind ID, attempt, source SHA, byte count and duration without expanding generic `Provenance`.

Audio provenance remains `source_kind=provider`, Google/Lyria identity, provider and local request IDs, prompt version and immutable manifest dependency. Derived provenance identifies `tovitunes.production_handoff`, version `production_storyboard_v1` and pinned input IDs.

## Audio SHA verification

Original candidate-2 MP3, receipt, persisted output, analysis, timing and copied production master SHA-256 all agree:

`06820bff29d0127ae3b25e53c73ba96b874732916acee5b4800e688c1d73e991`

Production byte count: **923624**. AssetStore retained an immutable byte-for-byte MP3 copy. No trim, rewrite, transcode, normalization or regeneration.

## Alignment artifact

Artifact: `9edd8b5e-7554-4ca7-a945-786d9dfa30f1` (`audio_alignment/main`).

Schema version 1 stores master ID/SHA, source blind ID/version, actual duration, 36 exact canonical word timings, 7 exact lyric-line timings, 4 measured section timings and measured pre/post-lyric intervals. CTC scores are omitted; alignment does not reevaluate QA.

Pre-lyric: `0.0 -> 2.462`. Post-lyric: `29.319 -> 38.164897959183676`. These are measured edges, not claims of purely instrumental regions.

## Beat artifact

Artifact: `705c82aa-0fd0-47ca-ba22-1265e6c76f2d` (`beat_analysis/main`). Estimated BPM: **108.04321728691477**.

Detector `Beat This` version `1.1.0`, model `final0`, checkpoint `sha256:8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331`. Exact admitted global arrays contain 61 beats and 16 downbeats. No new beat inference was run.

## Storyboard schema

Storyboard artifact: `25937e67-b373-42e5-a816-9d21a732d96c` (`timed_storyboard/main`), schema version **1**.

`domain/storyboard.py` defines the small immutable production contracts. TimedStoryboard pins episode, audio master, alignment, beat artifact, audio SHA/duration, Tovi pack, template ID/hash and scenes. Scene fields cover kind/section, actual and visual intervals, lyric, visual focus, required props, semantic action, lesson target and beat/downbeat index slices. No rendering machinery or image prompts.

`load_snapshot()` reads this real storyboard and validates episode/audio references, exact lyric intervals and beat association. Historical tiny index JSON remains a read-only compatibility format. For imported music, the planner recognizes the selected audio plus its matching immutable provider handoff manifest; no new pre-music drafts are required. Real render planning next requests `scene_image:intro`.

## Storyboard template

Creative mapping is data in `brands/tovitunes/storyboards/colors_red_v1.yaml`, ID `colors_red_v1`. Python applies it deterministically with no LLM.

Template byte SHA-256: `5da5ed5e66ef8e96f2f04c2fd2f5732cea864c3c252650b2417bc58c1c067625`. `.gitattributes` preserves template bytes across platforms.

Required prop IDs are `red_swatch`, `red_apple`, `red_ball`; each identifier declares exactly one object. Semantic actions remain a small controlled vocabulary. Only pinned Tovi appears. No prop/character/image assets were generated.

## Full scene table

| Scene | Start (s) | End (s) | Section | Lyric | Visual focus | Required props | Tovi action | Beats / downbeats |
| --- | ---: | ---: | --- | --- | --- | --- | --- | ---: |
| `intro` | 0.0 | 2.462 | pre_lyric | — | Tovi greets in a clean playful setting. | — | enter | 5 / 2 |
| `lyric_01` | 2.462 | 6.925 | hook | Red, red, look ahead! | Tovi directs attention toward the clearly red lesson swatch. | red_swatch | point | 8 / 2 |
| `lyric_02` | 6.925 | 11.147 | hook | Red is a color, yes, red! | A clear red swatch makes the lesson color unambiguous. | red_swatch | present | 7 / 1 |
| `lyric_03` | 11.147 | 15.57 | teaching | A red apple, round and bright. | Exactly one clearly red apple is shown prominently. | red_apple | present | 8 / 2 |
| `lyric_04` | 15.57 | 20.193 | teaching | A red ball rolls into sight. | Exactly one clearly red ball rolls into view. | red_ball | point | 8 / 2 |
| `lyric_05` | 20.193 | 24.636 | reinforcement | Red, red, what do you see? | Tovi gestures toward the previously introduced red apple and red ball. | red_apple, red_ball | question | 9 / 3 |
| `lyric_06` | 24.636 | 29.079 | reinforcement | Red is a color, sing with me! | Tovi sings with the red swatch and familiar red objects visible. | red_swatch, red_apple, red_ball | sing | 8 / 2 |
| `lyric_07` | 29.079 | 29.319 | ending | Red! | Tovi celebrates with simple emphasis on the red swatch. | red_swatch | celebrate | 0 / 0 |
| `outro` | 29.319 | 38.164897959183676 | post_lyric | — | Tovi celebrates and settles; no new educational claim or spoken content. | — | celebrate | 8 / 2 |

All seven sung intervals remain exactly the admitted line start/end values. In this source, neighboring line edges already meet; for inputs containing musical gaps, a lyric visual scene persists to the next measured lyric start while retaining its own unchanged sung end.

## Beat/downbeat association

Scenes store zero-based `[start_index, end_index)` slices of the selected beat artifact. Scene timing is half-open, with the final scene including a beat exactly at track duration. No global array duplication, beat snapping or lyric interpolation.

| Scene | Beat indices | Downbeat indices |
| --- | --- | --- |
| `intro` | `[0, 5]` | `[0, 2]` |
| `lyric_01` | `[5, 13]` | `[2, 4]` |
| `lyric_02` | `[13, 20]` | `[4, 5]` |
| `lyric_03` | `[20, 28]` | `[5, 7]` |
| `lyric_04` | `[28, 36]` | `[7, 9]` |
| `lyric_05` | `[36, 45]` | `[9, 12]` |
| `lyric_06` | `[45, 53]` | `[12, 14]` |
| `lyric_07` | `[53, 53]` | `[14, 14]` |
| `outro` | `[53, 61]` | `[14, 16]` |

Every measured beat/downbeat belongs to one scene. The short final `Red!` scene contains zero beats/downbeats; its timing is preserved. The final detected beat is at 33.54 seconds; no beat was invented for the remaining tail.

## Coverage validation

- Scene count: **9**, including **7** canonical lyric scenes, positive intro and positive outro.
- Coverage: **0.0 -> 38.164897959183676 seconds**.
- Gaps: **0**. Overlaps: **0**. Scene IDs: unique, safe and deterministic.
- Every canonical lyric line occurs exactly once in order, with exact admitted lyric timestamps.
- Nonnegative positive scene ranges and complete boundary equality are validated by the domain model.

## Educational validation

- Target concept remains red and objective remains `colors.red.identify`.
- Apple line requires exactly one `red_apple`; ball line requires exactly one `red_ball`.
- Controlled props/lesson targets cannot assign another teaching color.
- Only pinned Tovi is permitted; extra characters are rejected.
- Intro/outro contain no lyrics or new teaching claim.

## Artifact dependency graph

All dependencies use existing immutable artifact IDs and pinned input SHA-256 values:

```text
audio_master (ace1ea8d-6a31-47a2-bb2a-2a895ecd9604)
  -> production_handoff (2aa71a24-8251-4176-a4c2-09e680af5fc5)
audio_alignment (9edd8b5e-7554-4ca7-a945-786d9dfa30f1)
  -> audio_master
beat_analysis (705c82aa-0fd0-47ca-ba22-1265e6c76f2d)
  -> audio_master
timed_storyboard (25937e67-b373-42e5-a816-9d21a732d96c)
  -> audio_master, audio_alignment, beat_analysis
```

No second asset store, provenance system or storyboard database was created. Each JSON passed semantic validation and existing AssetStore ingestion/immutable-file validation before machine approval and selection.

## Idempotency proof

The new CLI path ran twice against the same authoritative state, with provider/network entry points blocked. The first run appended the episode and five production artifacts (four requested artifacts plus the small source manifest). The second run reported **reuse for every artifact**, returned identical artifact IDs and preserved all table row digests and selection timestamps exactly. A subsequent read-only plan also reports reuse throughout.

All repeat-run row sets were compared, including approvals, rights decisions, artifact validations and selections. No duplicate versions or decisions were appended.

## Historical immutability

Full-table canonical row-digest multisets were taken before and after the handoff; every preexisting row is unchanged. Every `music_*` table is exactly unchanged, including requests, receipts, both outputs, evaluations, decisions, all eight analyses and all eight timing rows. Candidate-1 versions 1–5 and candidate-2 versions 1–3 are preserved.

Original retained MP3 hashes before and after:

- Candidate 2: `06820bff29d0127ae3b25e53c73ba96b874732916acee5b4800e688c1d73e991`.
- Candidate 1: `613d42c4cbbb133366cde4378585197a82246b3fe7106eb69996466a7e0dd9c0`.

| Production table | Before | After | Appended |
| --- | ---: | ---: | ---: |
| `episodes` | 0 | 1 | 1 |
| `episode_character_packs` | 0 | 1 | 1 |
| `artifact_versions` | 68 | 73 | 5 |
| `artifact_dependencies` | 108 | 114 | 6 |
| `artifact_selections` | 42 | 47 | 5 |
| `artifact_validation` | 68 | 73 | 5 |
| `rights_decisions` | 114 | 119 | 5 |
| `approval_decisions` | 116 | 127 | 11 |

The 11 approval rows are five ingestion-pending decisions, five explicit technical artifact approvals and one curriculum planning decision. Rights rows record unknown. No music decisions were appended. Existing catalog/character revisions and visual benchmark rows are unchanged; the lease is released.

## Provider-call audit

- Lyria/music generation calls: **0**.
- Image generation calls: **0**.
- Video generation calls: **0**.
- LLM calls: **0**.
- Provider-resume calls: **0**.
- New analysis or policy reevaluation calls: **0**.
- Forbidden provider/network/analysis entry-point attempts: **0**.

Outbound socket connection entry points, generation, provider-resume, analysis, music policy reevaluation, visual benchmark generation and draft-generation entry points were patched to raise around both real CLI executions. Unchanged music request/receipt/output rows and original audio hashes corroborate the audit. Candidate 3 was not generated; candidate 2 was not regenerated.

Validation:

- `uv run pytest -q`: **367 passed**, including 39 new offline production cases.
- `uv run ruff check .`: passed.
- `uv run mypy src`: passed, 44 source files.
- `git diff --check`: passed.
- Tovi `character-pack validate-lock`: passed, **48 artifacts**.

CI/dependencies and frozen music implementation are unchanged. Production tests block providers/network and analysis entry points and require no music models, GPU, CUDA or downloads.

## Rights state

**Unknown** on the audio master and all derived production artifacts. `music_outputs.approval_status` remains **pending**, and source rights remain **unknown**.

`technical_production_master_v1` means technical acceptance for storyboard/render development. `canonical_curriculum_v1` means pinned-objective acceptance for technical planning. Neither grants commercial rights, music-output approval, final-video approval or publication approval. All five production IDs remain in the planner's uncleared transitive rights set. **Publication remains blocked.**

## Next renderer inputs

The next phase can consume the selected master `ace1ea8d-6a31-47a2-bb2a-2a895ecd9604`, alignment `9edd8b5e-7554-4ca7-a945-786d9dfa30f1`, beat analysis `705c82aa-0fd0-47ca-ba22-1265e6c76f2d` and storyboard `25937e67-b373-42e5-a816-9d21a732d96c` through AssetStore, without querying benchmark internals. The storyboard declares the pinned Tovi pack, exact scene intervals, three prop IDs, semantic actions and beat index slices.

No animation, compositor, image/video generation, subtitle/karaoke system, visual QA, Creative Director, publication or rights confirmation is included. Reusable animation/compositor libraries are evaluated in the next phase, as directed.

Local output copies retain the exact storyboard/alignment/beat/source-manifest JSON, both command reports and the historical/idempotency audit; production truth remains the existing live AssetStore/SQLite graph. DBs, audio, model caches and local paths/configuration are not committed.

TIMED_STORYBOARD_READY_FOR_ANIMATION
