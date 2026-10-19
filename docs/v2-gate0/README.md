# ToviTunes V2 — Gate 0 decision

**BLOCKED pending owner approval** of the benchmark/budgets and file-ownership
contract. The baseline is prepared for review; this status does not authorize
Gate 1, Gate 2, another task, provider effects, a merge or publication.

| Gate report field | Evidence / result |
|---|---|
| Work package | Gate 0 only: baseline, read-only architecture/protected-state audit, ownership and benchmark contract |
| Branch / audited code | `codex/v2-gate0-baseline`, based on exact PR #49 `790da5502a966675b94a32838e303a97fb16503e` |
| Delivery / actual changes | Draft documentation PR targets `fix/native-comfy-crash-recovery`; changes are confined to `docs/v2-gate0/`. The exact delivery head and hosted check snapshot are recorded in the final local handoff and PR. |
| General findings / correction | Existing integrated production/recovery architecture is retained. Confirmed stage-independent service startup and legacy config drift are documented for their approved gates. No system implementation correction is claimed or made in Gate 0. |
| Cross-category evidence | No new episode/art/audio is produced. Existing indoor/outdoor/pale-art and normal/preview regression contracts remain unchanged. Actual cross-category creative/editorial acceptance belongs to later approved gates. |
| Provider / resource effects | **Zero provider calls**, no model inference/loading, no service start/stop, no production migration/mutation, no episode resume, no benchmark, no merge or publication. New isolated dependency environment and disposable CPU/FFmpeg test artifacts only. |
| Validation | Ruff, strict Mypy (121 modules), character lock (48), 31 native-crash regressions and all 1,187 offline tests passed. Baseline exact-head hosted Windows CI succeeded; delivery-head status is reported separately. |
| Protected state | Three database snapshots / 955 registered artifacts and 2,252 protected paths preserved; 30 required installed model/config files unchanged. All 85 illustration profiles and 20 retained storyboard/render formats checked without production writes. |
| Remaining approval | Owner must explicitly accept the benchmark evidence rubric, proposed provider ceilings and sequential file ownership. Roadmap text is preserved as supplied; no scope amendment is made. |
| Decision | **BLOCKED**; stop at Gate 0. |

## Deliverables

- **A — [Baseline report](BASELINE.md):** exact PR/commit ancestry, lock/config/model
  assumptions, schemas/migration replay, validation and reproducible commands.
- **B — [Architecture map](ARCHITECTURE.md):** components, source entry points,
  data/provider boundaries, dependencies, verified functions and retained overlap.
- **C — [Ownership matrix](OWNERSHIP.md):** six gate boundaries, named source files,
  branch targets and whole-file handoff restrictions.
- **D — [Benchmark specification](BENCHMARK.md):** fixed three categories, unchanged
  3/3 release requirements, evidence/human review and clearly marked budget proposals.
- **E — [Protected-state audit](PROTECTED_STATE.md):** original/acceptance databases,
  artifact/character/config/request/model preservation and evidence custody.
- **F — Gate decision:** this page; [minimal risk register](RISKS.md) covers only
  approved roadmap risks. Machine-readable supporting evidence is under `evidence/`.

The authoritative [Locked Roadmap v1.0](LOCKED_ROADMAP_V1.0.md), supplied by the owner
in this Gate 0 chat on 19 October 2026, is preserved verbatim in wording/version.
Markdown file storage/newline normalization adds no scope or approval. Approval of
the roadmap itself is separate from approval of this Gate 0 contract and from any
later live-generation batch.
