# Visual provider benchmark: offline protocol v1

This protocol prepares the ten-scene comparison from the development plan. It is **not** a generated image set or a provider ranking. Run it only after the canonical Tovi pack has approved reference images, palette, identity rules and commercial-use evidence. The initial candidates named in the plan are Gemini 3.1 Flash Image and GPT Image 2.5; record the exact available product/model identifiers and terms at run time.

## Fixed inputs

Use the same approved `CharacterAssetPack` revision and the same front, three-quarter and profile reference artifacts for both providers. Pin their artifact IDs and hashes in every scorecard. Hold palette, negative identity rules, intended portrait framing and the scene brief constant. If a provider needs different prompt syntax or cannot accept the same reference count, record the translation and limitation; do not claim a controlled comparison where inputs differed materially.

The ten locked briefs are in [`benchmarks/visual/cases.v1.yaml`](../benchmarks/visual/cases.v1.yaml). They cover a red apple, exactly three stars, a triangle surprise, flight, profile view, another original character, bedroom, playground, pointing and holding a prop. Generate at least two independent requests per scene and provider. Keep failed requests and every returned candidate. Label images with blind IDs and retain the provider mapping separately.

These images test environment/prop production and difficult Tovi poses. A successful generated Tovi image may be used only as an individually reviewed special asset; it does not become the canonical character or replace sprite animation. Ordinary recurring motion remains tied to the approved pack.

## Review and measurement

Two reviewers score each decoded image without seeing the provider. A third resolves any 2-point or larger disagreement on an axis. The five image axes use 0–4 ratings: 0 broken; 1 major errors; 2 substantial repair; 3 usable; 4 excellent. Use the mean of two scores when their difference is below 2, or the median of three after adjudication. Record a cropped evidence region or clear note for every identity or teaching error. The operator separately records reference-input control, editability, request cost and latency.

| Axis | Weight | Review question |
| --- | ---: | --- |
| Character identity | 30 | Does Tovi retain the approved silhouette, proportions, tuft, eyes, beak, wings and palette? |
| Teaching accuracy | 25 | Is the target object/color/shape/count exactly correct and easy to see? |
| Composition | 15 | Is the pose, interaction and portrait layout useful for a Short? |
| Reference fidelity | 10 | Does the output follow the pinned views and identity constraints without copying a reference composition mechanically? |
| Image quality | 10 | Are anatomy, edges, lighting, detail and required transparency clean enough to use? |
| Production fit | 10 | Can the accepted image be revised, layered/cropped and reproduced with the tested controls? |

Weights are machine-readable in [`rubric.v1.yaml`](../benchmarks/visual/rubric.v1.yaml). `weighted_score = Σ(weight × rating / 4)`. Character identity and teaching accuracy must each score at least 3 for a candidate to count as usable. A wrong target color, count or shape, serious identity drift, unsafe image, unusable file or unlicensed reference is a hard failure regardless of weighted score. Unknown output rights blocks production selection, even when the image is worth scoring.

Report per-scene usable counts, usable outputs per distinct request, usable outputs per actual spend, median latency and the most common repair reasons. For teaching color, count and shape, use exact human verification first. Automated thresholds must be calibrated from ToviTunes' accepted/rejected examples before being treated as pass/fail evidence.

Fill one [`scorecard.template.yaml`](../benchmarks/visual/scorecard.template.yaml) per request/output pair. The benchmark ends with a dated human decision describing which image tasks a provider can support, rights evidence, unresolved controls and whether its output is limited to backgrounds/props or reviewed special poses. No provider is selected from this blank protocol.

