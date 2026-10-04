# Colors–Red publication rights evidence

## Frozen selection verified on 2026-10-05

The local production database selected episode `colors-red-001`, V4 final render
`bdda4b54-c487-429c-837e-94e52e7d4af3` with SHA-256
`e0ce789eb49a40471dd1927ef19d9d9239e4b031fd052893fa25b30afebffafa`,
manifest `ddf6f790-86fa-479d-9318-88c3f077ce0e`, and passing media QA
`7fbe275a-b726-4e9b-91aa-ffc5beee8d7b`. The selected lesson-object manifest
is `2e5b6568-3126-4ae8-9580-2b2b22c89507`. Its selected canonical apple is
`d9d2adb6-3753-4c63-86ca-7a7954b8ec6e`, and ball is
`2999861d-41aa-4479-acba-b1d405527f13`. Their source and candidate IDs and
hashes are in [PROP_ART_V2.md](../../PROP_ART_V2.md). These selections match the
frozen pilot described there. No media or lesson object was regenerated.

`MetadataWriter` selected publication metadata
`f79159eb-ab40-4b75-bdbb-ddd839e76350`, fingerprint
`ce4f332bd9acf452afea920730a02a07afa7277bca73466bf85a542f99bc4162`.
The exact 92-artifact selected release graph is recorded in
[COLORS_RED_RELEASE_GRAPH.json](COLORS_RED_RELEASE_GRAPH.json). At this audit,
37 nodes had `commercial_use_confirmed`, 48 had `unknown`, and seven had
`review_required`. Private test preflight passed; public release preflight was
blocked solely by commercial rights. No private upload was performed.

## Evidence reviewed

| Source family | Evidence | What it establishes | Remaining question |
| --- | --- | --- | --- |
| Google Lyria 3 Pro audio | [Google Lyria 3 documentation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/lyria/lyria-3), [Google Cloud service terms](https://cloud.google.com/terms/service-terms) | Google's model documentation expressly permits customers to elect commercial or production use of this preview offering; Google's terms say it does not assert ownership of new IP in generated output. | Confirm the production account and input lyrics were used under those terms. |
| Google Gemini 3 Pro Image environment | [Model documentation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-pro-image), [Google Cloud service terms](https://cloud.google.com/terms/service-terms) | The selected model is listed as GA, and the Cloud terms address generated output ownership. | Confirm the production account and source references were authorized. |
| Qwen Image 2.1 apple and ball | [Qwen official license](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/LICENSE), [Qwen Developers clarification referenced by the operator](https://x.com/QwenDevs/status/2101917379785838660) | The official license grants use of model materials for research and evaluation and requires a separate license for commercial use. The X statement has been cited as a clarification about generated images; its text was not independently retrievable in this audit. | Whether the project's local inference for commercial production was covered by separate permission or an authoritative clarification of the model-use restriction. |
| Tovi character sources | [Character pack intake](../CHARACTER_PACK_INTAKE.md), [OpenAI terms](https://openai.com/policies/row-terms-of-use/) | Known ChatGPT-generated role sources already have evidence-backed commercial decisions. | The selected `original_profile` and `original_banner` references remain of undocumented provenance and historically `unknown`. |

The operator stated on 2026-10-05 that additional rights evidence had been found,
including a Qwen Developers X statement, but that evidence is not recorded in a
source accessible to this repository. This document preserves the distinction
between that statement and verified, artifact-specific clearance. Historical
`review_required` and `unknown` decisions remain valid history; later evidence
can resolve them by appending new decisions.

## Closeout procedure

`tovitunes.publication.rights.closeout_rights` accepts a reviewed JSON file
containing the episode key, selected render ID/SHA, selected metadata ID and
fingerprint, the complete release graph of artifact IDs and SHAs, and one
artifact-specific evidence entry per uncleared graph node (55 in this snapshot).
Each entry must supply the
artifact SHA, kind, slot, actor, evidence URI, rationale, and decision time.
It validates all selected immutable files, dependency pins, graph membership,
approvals, render/metadata identity, and evidence completeness before calling
`AssetStore.record_rights(RightsDecision(...))` under
`tovitunes_publication_rights_v1`. It appends only decisions for selected release
dependencies that are not already commercially cleared. Approval rows are not
modified. A changed production graph or missing evidence stops the operation.

The JSON graph file is a review template, not a rights assertion. Its blank
`actor`, `evidence_uri`, `rationale`, and `decided_at` fields must be completed
with evidence appropriate to each artifact or provenance family. The closeout
command rejects those blanks. The saved graph is only valid while its selected
IDs, hashes, metadata fingerprint, and dependency set still match the database.

**Current closeout state:** no new `commercial_use_confirmed` rows were appended
by this release work. The commercial release gate remains blocked until the
open questions above are resolved and recorded for the exact selected graph.
