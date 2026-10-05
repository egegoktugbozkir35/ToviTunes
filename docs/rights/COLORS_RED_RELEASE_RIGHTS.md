# Colors–Red publication rights evidence

## Frozen release inputs

For `colors-red-001`, the selected V4 final render remains
`bdda4b54-c487-429c-837e-94e52e7d4af3`, SHA-256
`e0ce789eb49a40471dd1927ef19d9d9239e4b031fd052893fa25b30afebffafa`.
The selected render manifest remains `ddf6f790-86fa-479d-9318-88c3f077ce0e`
and passing media QA remains `7fbe275a-b726-4e9b-91aa-ffc5beee8d7b`.
The only changed selection is `publication_metadata/main`: operator-approved
artifact `0eca2f8e-eda2-4b52-bd06-a063a6681253`, fingerprint
`3b5f04dcddba55ed211bb56a1a2a0b367bbe538d10f8b278f61ed090d1baa2a3`.
The previous NVIDIA/Kimi artifact `f79159eb-ab40-4b75-bdbb-ddd839e76350`
remains immutable history and is unselected. No music, environment, lesson
object, scene, or render was regenerated.

The exact selected 92-artifact dependency graph is frozen in
[COLORS_RED_RELEASE_GRAPH.json](COLORS_RED_RELEASE_GRAPH.json). Its immutable
files, approvals, dependency SHA pins, manifest binding, media QA, and metadata
binding all remain subject to release preflight. The old Kimi artifact is absent
from that active graph.

## Rights applicability

Policy `tovitunes_rights_inheritance_v1` identifies 20 direct rights-bearing
roots and 72 derived artifacts in the selected graph. Provider-supplied
publication metadata is a direct root because its provider-generated text would
be published. Operator-authored metadata is an internally approved manual
artifact and requires no external provider licence decision. It still needs
immutable SHA, selected status, current approval, render binding, schema
validation, and pinned dependency SHAs. An explicit `blocked` rights decision
on any artifact remains blocking.

The operator-approved [metadata record](COLORS_RED_OPERATOR_METADATA.md)
contains the exact title, description, and tags. It was ingested with manual
provenance and approved by `human:operator`, with zero creative provider calls.
The old Kimi metadata was not cleared under NVIDIA trial terms; it simply no
longer enters the selected release graph.

## Direct roots and closeout

| Family | Direct roots | Current state |
| --- | ---: | --- |
| OpenAI-generated Tovi source references, including original profile/banner | 13 | `commercial_use_confirmed` |
| Qwen red apple and red ball source outputs | 2 | `commercial_use_confirmed` |
| Google Gemini environment source plates | 4 | `commercial_use_confirmed` |
| Google Lyria audio master | 1 | `commercial_use_confirmed` |

The operator confirmed on 2026-10-05 that original Tovi profile
`db50d001-33ea-4f1c-8c0d-36e2760a2c7a` and banner
`de41c057-abc7-45f4-a06f-e7359ff1f350` were generated through ChatGPT /
OpenAI image generation, and approved their commercial use in ToviTunes.
The exact source files were SHA-matched against the intake recipe, artifact
lock, and ingested roots. The [attestation](TOVI_ORIGINAL_SOURCE_ATTESTATION.md)
records the evidence and its limits. Each `commercial_use_confirmed` decision
was appended via `AssetStore.record_rights(RightsDecision(...))`; historical
`unknown` rows were retained. The evidence URI references that committed
attestation, which cites the existing
[OpenAI Terms of Use](https://openai.com/policies/row-terms-of-use/).

The eleven previously cleared OpenAI-generated Tovi sources retain their
earlier evidence-backed decisions, as described in the
[character intake record](../CHARACTER_PACK_INTAKE.md). The two Qwen sources,
four Gemini environment sources, and Lyria audio master retain the reviewed
provider evidence described in the previous release closeout.

`reviewed_graph_template()` now reports zero uncleared direct roots. The
provider-free `evaluate_release()` returns `render_ready=true`,
`private_test_upload_allowed=true`, and `public_release_allowed=true` against
the current production database. The evidence supports project provenance and
permission for release purposes; it does not establish copyrightability,
uniqueness, or trademark clearance.

To refresh a read-only snapshot after a future selection change, run:

```powershell
python -m tovitunes.publication.rights --config config.yaml --snapshot colors-red-001
```
