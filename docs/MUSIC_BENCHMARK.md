# Music benchmark: offline rubric v1

This is a frozen evaluation design, not a provider result. The candidates named in the development plan are Suno, Eleven Music and Lyria 3. No account access, purchase, generation, rights clearance or provider selection is implied by this document. Record the exact product/model/tier and current terms when the benchmark is actually run.

## Controlled test

Use the five original test cards in [`benchmarks/music/cases.v1.yaml`](../benchmarks/music/cases.v1.yaml). They stress `red`, `blue`, `three`, `circle` and `happy` in a 35-second English preschool song. Keep the lyrics, requested duration, creative brief and no-imitation instruction identical for each provider. Use each provider's closest supported input mode, and record any translation or unsupported control. Do not claim exact-lyrics, BPM, seed, section-length or timestamp support unless the tested interface actually exposes it.

Generate at least two independent requests per card and provider. Keep every response and output, including unusable or failed attempts; otherwise usable-output rates will be inflated. Store raw files through the artifact store with request IDs, provider/model, prompt version, generation time, costs, rights state and selected input hashes. Label clips with random IDs for blind listening. Preserve the provider mapping separately. Count distinct requests and individual output clips separately when a request returns several clips.

Two reviewers independently score the first five output axes without seeing provider or model. A third reviewer resolves any 2-point or larger disagreement on an axis. For those five axes, use the mean of two scores when they differ by less than 2, or the median of three after adjudication. An operator separately scores controllability and production fit from the tested interface and output files, with evidence. Do not average away a wrong teaching word, unsafe lyric or unusable file. A reviewer records the heard words and evidence time span for pronunciation or lyric mistakes; speech recognition may assist but does not replace listening. Use the blank [`scorecard.template.yaml`](../benchmarks/music/scorecard.template.yaml) for every request/output pair.

## Hard gates

A clip is **unusable** for the pilot if it fails to decode, is materially truncated or silent, changes or omits a target teaching word, teaches a false fact, contains preschool-inappropriate material, imitates a named artist or copyrighted character, or has severe clipping/distortion. A clip with unknown commercial-use rights may be measured, but it cannot be selected as a production master. Record source URLs or account-specific evidence for rights; a marketing statement alone does not establish clearance for the actual tier and use.

Duration target is 30–45 seconds with the first intelligible target word by 10 seconds. Report deviations even if the clip is otherwise worth reviewing. This initial threshold is a pilot hypothesis and should be revisited using accepted ToviTunes episodes.

## Scoring

Each axis receives an integer 0–4: **0** broken or absent; **1** major defects; **2** mixed, requires repair; **3** production-usable; **4** exceptionally clear and polished. The weights in [`rubric.v1.yaml`](../benchmarks/music/rubric.v1.yaml) sum to 100. Compute `weighted_score = Σ(weight × rating / 4)`. Evidence is mandatory for scores 0–2 and 4.

| Axis | Weight | What reviewers assess |
| --- | ---: | --- |
| Target-word pronunciation and intelligibility | 25 | Every target word is clearly pronounced, especially at the first occurrence and chorus. |
| Lyric adherence | 15 | Requested words, order, repetitions and sections survive without unapproved replacements. |
| Educational and preschool fit | 15 | Meaning is correct, easy to follow, age appropriate and free of confusing distractors. |
| Musical appeal | 15 | Memorable melody, warmth, rhythm, energy and comfortable repetition. |
| Audio quality | 10 | Clean vocal, balanced mix, no severe artifacts, silence or clipping. |
| Controllability | 10 | Tested input controls and revisions work predictably; record unsupported controls. |
| Production fit | 10 | Useful duration/structure, editability, timing data or stems where available. |

The seven weighted axes judge the output and the tested workflow. Rights remains an independent release gate. Cost and latency are reported as measurements rather than hidden in a subjective score: total billable cost, seconds to usable result, usable outputs/request, and usable outputs/actual spend. Record whether each attempt used a web UI or API. A manual UI result does not prove API automation.

For initial comparison, mark a clip **usable** only if all content and file hard gates pass and the first three axes each score at least 3. Report each provider's per-card results, median weighted score of usable clips, total usable count, usable/request, usable/cost, median latency, control gaps and rights evidence. Publish the raw denominators and any failed requests. Do not pick a provider from this small sample alone: first inspect the mistakes, subscription terms and ability to rerun or edit the accepted song.

## Review decision

The benchmark ends with a dated human decision that names the tested model/tier, accepts or rejects commercial terms with evidence, records the measurements and lists unresolved risks. A selected song still needs objective audio QA and a separate human song approval before it becomes the episode's audio master. Providers remain replaceable behind the `MusicProvider` boundary.

