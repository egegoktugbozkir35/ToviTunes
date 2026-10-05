# Colors–Red publication rights evidence

## Frozen release inputs

The local production database selects episode `colors-red-001`, V4 final render
`bdda4b54-c487-429c-837e-94e52e7d4af3` with SHA-256
`e0ce789eb49a40471dd1927ef19d9d9239e4b031fd052893fa25b30afebffafa`,
manifest `ddf6f790-86fa-479d-9318-88c3f077ce0e`, and passing media QA
`7fbe275a-b726-4e9b-91aa-ffc5beee8d7b`. The selected publication metadata is
`f79159eb-ab40-4b75-bdbb-ddd839e76350`, fingerprint
`ce4f332bd9acf452afea920730a02a07afa7277bca73466bf85a542f99bc4162`.
No media, music, lesson object, environment, or metadata was regenerated during
rights closeout.

The exact 92-artifact dependency graph is frozen in
[COLORS_RED_RELEASE_GRAPH.json](COLORS_RED_RELEASE_GRAPH.json). All 92 immutable
files, selections, approvals, dependency SHA pins, manifest bindings, and QA
records remain part of release preflight.

## Rights applicability

Policy `tovitunes_rights_inheritance_v1` separates the technical graph into 21
direct rights-bearing roots and 71 derived artifacts. A direct root is an
externally or provider-supplied creative source whose bytes or text enter the
published work. The policy currently recognizes non-deterministic
`audio_master`, `character_reference`, `environment_source_plate`,
`lesson_object_source`, `lyrics`, and `publication_metadata` artifacts.

Deterministic and normalized artifacts inherit eligibility through their pinned
dependencies. This includes lesson-object candidates, canonical objects and
their manifest; normalized environment plates and environment sets; audio
analysis and storyboard timing; character crops, sprites and animations; scene
images and motion; production handoff; render manifests; QA; and the final
render. Their own historical `unknown` or `review_required` rows are retained
but do not require duplicate legal evidence. An explicit `blocked` decision on
any artifact in an ancestor closure still blocks that artifact and its
descendants. A missing or changed dependency SHA, invalid file, changed
selection, or failed approval remains independently blocking.

`publication_metadata` is a direct root because its provider-generated title,
description, and tags are themselves published. The model's open license does
not by itself resolve use through NVIDIA's API service. NVIDIA's
[API Trial Terms](https://assets.ngc.nvidia.com/products/api-catalog/legal/NVIDIA%20API%20Trial%20Terms%20of%20Service.pdf)
limit trial API Generated Content to testing and evaluation unless a production
subscription applies. The repository does not establish which entitlement
covered this request, so the metadata root remains `unknown`.

## Direct roots and current state

| Family | Direct roots | Current state |
| --- | ---: | --- |
| OpenAI-generated Tovi source references | 11 | `commercial_use_confirmed` |
| Original user-supplied Tovi profile/banner | 2 | `unknown` |
| Qwen red apple and red ball source outputs | 2 | `commercial_use_confirmed` |
| Google Gemini environment source plates | 4 | `commercial_use_confirmed` |
| Google Lyria audio master | 1 | `commercial_use_confirmed` |
| NVIDIA/Kimi publication metadata | 1 | `unknown` |

Eighteen of 21 direct roots are cleared. The three remaining decisions in the
JSON closeout template are:

- `db50d001-33ea-4f1c-8c0d-36e2760a2c7a`, `source_original_profile`;
- `de41c057-abc7-45f4-a06f-e7359ff1f350`, `source_original_banner`;
- `f79159eb-ab40-4b75-bdbb-ddd839e76350`, publication metadata.

The character intake recipe and lock call the first two files `user-supplied`
and explicitly state that their OpenAI generation provenance is not documented.
Human confirmation must identify their creator/source and the operator's right
to use them commercially. The metadata root needs evidence that the NVIDIA API
request was covered by a production subscription or other permission allowing
production use of Generated Content.

## Appended evidence

Seven `commercial_use_confirmed` decisions were appended through
`AssetStore.record_rights` under `tovitunes_publication_rights_v1`; no historical
row was changed or deleted:

- Qwen source outputs `1623fb15-6c0b-4b1c-99c2-10c33e8d2ff4` and
  `2a4bdf50-1985-4817-8c7d-5914f07ed908`, using the operator-reviewed
  [official Qwen Developers clarification](https://x.com/QwenDevs/status/2101917379785838660).
  Their normalized candidates, canonical apple/ball, manifest, scenes, and
  render inherit these decisions.
- Lyria audio master `ace1ea8d-6a31-47a2-bb2a-2a895ecd9604`, using Google's
  [Lyria 3 documentation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/lyria/lyria-3)
  and [Cloud service terms](https://cloud.google.com/terms/service-terms).
- Gemini environment sources `c3dd122b-3965-443e-8178-2e6087ea19ef`,
  `e1aaa450-437a-4255-ada1-56f080de919c`,
  `e906f527-9217-4827-bef0-5af5f5e4f234`, and
  `f46e4c77-87a9-4b1b-8dd3-9f0c29f82550`, using the official
  [Gemini image documentation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-pro-image)
  and Cloud service terms.

The eleven generated Tovi sources retain their earlier evidence-backed OpenAI
decisions. The [character intake record](../CHARACTER_PACK_INTAKE.md) explains
their generation IDs, dependency links, and the scope of the
[OpenAI terms evidence](https://openai.com/policies/row-terms-of-use/).

## Closeout procedure

`reviewed_graph_template()` freezes the complete graph SHA map, the direct-root
classification, and all 71 derived IDs. Its `decisions` object contains only
uncleared direct roots. `closeout_rights()` rejects selection, render, metadata,
hash, graph, or applicability drift; rejects evidence for an arbitrary derived
artifact; requires actor, evidence URI, rationale and decision time; and appends
through `AssetStore.record_rights(RightsDecision(...))`.

Generate a fresh template with:

```powershell
python -m tovitunes.publication.rights --config config.yaml --snapshot colors-red-001
```

After completing the three evidence entries, apply them with:

```powershell
python -m tovitunes.publication.rights --config config.yaml --evidence reviewed.json
```

Private preflight currently passes. Public preflight remains blocked by exactly
the three unresolved direct roots above.
