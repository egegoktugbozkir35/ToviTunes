# Production integration V1 acceptance

This branch integrates Visual Quality V1 r3 and Studio Reliability V1 on the
verified production baseline. The normal renderer remains the default. The r3
strategy requires explicit `visual_quality=True` and `local_preview=True`.
Nothing is merged to `main` and no publication is authorized by this acceptance.

## Sources and integration

| Track | Exact source commit |
| --- | --- |
| PR #44, production baseline | `f78dcdf1ebb19865552748023b8c9a91bf4829ba` |
| PR #45, Visual Quality V1 r3 | `e68f696d03e68263dbe60a04c4f67d1031f3d408` |
| PR #46, Studio Reliability V1 | `9592ba1ee686f8a6a0ea5ac79684f5875291633c` |

All three remote heads were rechecked on 17 October 2026 and matched. Both
features were integrated using non-fast-forward merges, preserving ancestry.
There were no merge conflicts. The shared creative-production test file retains
the renderer additions from #45 and the real-fixture recovery wait from #46.
No source-branch rewrite or production configuration change was required.

## Combined architecture audit

Studio continues to use `build_orchestrator` and the saved production target and
reference. Its readiness projection distinguishes local presence/configuration
and endpoint reachability from successful generation. Recovery retains admitted
milestones and durable run/episode identity. Frontend review links depend on
available selected media, independently of publication approval.

Fresh topic choice retains novelty history, learning briefs and bounded NVIDIA
creative fallback. Music requests retain ACE-Step task identity and immutable
receipts. Decoded audio and AudioClock remain duration authorities. The existing
Qwen ComfyUI adapters admit illustration and environment sources into the
artifact store; Storyboard V3 pins the resulting selected sources.

Studio changes do not alter the renderer API. The renderer's default is still
`tovitunes_creative_render_v1` for V3 input. Historical V1, r1 and r2 manifests
remain admitted by the parser; r3 is an additional version literal. Preview
artifacts use separate slots and require preview admission. Metadata and media
QA consume the production selection through the same services as before.

`ProductionTarget.RENDER` includes CREATIVE through METADATA. RELEASE and YOUTUBE
are absent from that target. The fresh acceptance must call Generate + Render
once, then continue only its retained identity if recovery is needed. A separate
verified data copy is required for the opt-in evaluation; it must reuse exact
music and artwork without replacing the production selection.

## Validation and acceptance status

Ruff, strict Mypy (121 source files), and the character lock (48 assets) pass in
an independent Windows Python 3.11 environment installed from `uv.lock` with the
documented dev, video-render, web and youtube extras. Focused tests, the complete
offline suite, Windows CI, read-only preflight and live acceptance are pending.

The final local handoff will record genuine provider outcomes, technical QA,
visual comparisons, reuse evidence and preservation checks. Until those checks
complete, fresh production readiness is **not established**.
