# Gate 0 reproducible baseline

Audit date: 19 October 2026, Europe/Istanbul. Repository:
`egegoktugbozkir35/ToviTunes`. Audited code is exact PR #49 head
`790da5502a966675b94a32838e303a97fb16503e`. Gate 0 branch:
`codex/v2-gate0-baseline`; draft PR base: `fix/native-comfy-crash-recovery`.
The worktree belongs to a new isolated bare clone; no existing Codex worktree is
reused or switched. Source, configuration, lockfiles, migrations and fixtures are
unchanged. Only `docs/v2-gate0/` is in the delivery diff.

## Verified PR ancestry

All six heads below were read from GitHub and match the assignment. All PRs are
open, draft and unmerged. Git reachability, not PR labels, establishes inclusion.

| PR | Exact head | Actual PR target | Reachability at #49 |
|---|---|---|---|
| #44 | `f78dcdf1ebb19865552748023b8c9a91bf4829ba` | `main` | Ancestor |
| #45 | `e68f696d03e68263dbe60a04c4f67d1031f3d408` | `refactor/creative-production-v3` | Ancestor through merge `245a47508b607e1396943fb556783d83f78a79fb` |
| #46 | `9592ba1ee686f8a6a0ea5ac79684f5875291633c` | `refactor/creative-production-v3` | Ancestor through merge `501f407` |
| #47 | `2ff081c03668aaba2d434546f97febd084dfb6c9` | `refactor/creative-production-v3` | Ancestor |
| #48 | `6b2898a34be85555c996c08a690d47bfd833767b` | `codex/production-integration-v1` | Direct parent of #49 |
| #49 | `790da5502a966675b94a32838e303a97fb16503e` | `codex/qwen-art-quality-v1` | Baseline |

```text
#44 --merge #45--> 245a475 --merge #46--> 501f407 --> #47 --> #48 --> #49 --> Gate 0 docs
```

#45 and #46 are independent descendants of #44, not ancestors of each other.
#47 contains both by actual merge ancestry. All 36 pairwise `merge-base
--is-ancestor` results and merge parents are in `evidence/repository.json`.
No PR is merged again and main is not treated as the integrated baseline.
Cloned `main` is `e0c6cb989264ececf5dd52192ff84de08e08486d` (merge of PR #43).
It is the merge base and an ancestor of #49; it does not contain #49.

## Dependencies, schema and configuration

- Sole dependency lock: `uv.lock` (189 resolved packages across optional groups).
  `pyproject.toml` requires Python ≥3.11 and MoviePy exactly 2.2.1 for video.
  Isolated validation installed **67 packages** with the same dev, video-render,
  web and youtube extras as CI; installed versions are retained in
  `evidence/dependencies.txt`. No service/CUDA environment was synchronized.
- Host: Windows build 26200; Python 3.11.9; uv 0.12.13; Git 2.56.0.windows.1;
  Node 24.14.0; FFmpeg/FFprobe 8.1. Ruff 0.16.8, Mypy 1.20.2, pytest 9.1.1,
  Pydantic 2.13.5, Pillow 11.3.0, MoviePy 2.2.1 and httpx 0.28.1 are locked.
- `RuntimeConfig` schema 1 uses immutable strict models (`extra=forbid`, finite
  numbers, paths resolved relative to config). The checked-in example, original
  production YAML, acceptance runtime JSON and isolated recovery JSON all validate
  against the current code. JSON-schema export emits existing Pydantic warnings
  that Path defaults are omitted as non-serializable; runtime validation succeeds.
- Example defaults: GLM `z-ai/glm-5.3` → GLM Flash → Nemotron → DeepSeek;
  endpoint-failure Ollama fallback off; Qwen Image 2.1 Q8 for both artwork paths;
  ACE-Step turbo / 0.6B LM / PyTorch / 8 steps / WAV / batch size 1. Illustration
  source 1024²; environment source 768×1376; Qwen 20 steps, CFG 1, Euler/simple.
  Publication/YouTube/auto-publish off, human review on, model downloads off.
- **Preserved configuration drift:** real production and acceptance configs still
  name Kimi as primary creative model and enable publication capabilities. They
  retain `auto_publish=false`, human review and no model downloads. Large-v3 is
  configured there, but V3 duration admission does not require it. The recovery
  config disables service auto-discovery and explicitly supplies conservative
  ComfyUI startup. None is rewritten or used to launch production by this audit.
  Current model advertisement/generation and publication access are not probed.
- Current source has migrations `0001`–`0030`. Production and older retained
  acceptance have 29; current interrupted-episode acceptance has 30. Every applied
  checksum matches exact-head source. No authoritative migration is performed.
  Replaying twice on independent SQLite backups gives 30, no existing-table row
  changes, no foreign-key violations and `quick_check=ok` in all three copies.

| Immutable source file | SHA-256 |
|---|---|
| `pyproject.toml` | `350083b8915f1724de0e8d09b3a89c62d2939f90b87a0ea34d225d380b36af8a` |
| `uv.lock` | `f0cc2e6f93ddaf991c3a3615ce858455292455de4acab5a1c48ebc488ab2b70b` |
| `config.example.yaml` | `1af47243f81f76f21f30495919b0267649b0f93d88c4a98f523e6e87bad4d4ba` |
| Qwen workflow | `fafd7fbc8bc2488e57bfe1b0a7e3a313804acd2798eabc6625e10f7550c5fa9e` |
| Character artifact lock | `1945ab690b5c9039ce1e9bc80cb9f5c3d4212d53494af0e2e553ac52571f4417` |

## Installed-model assumptions

The installed shared Qwen GGUF, encoder and VAE exist and were hashed read-only:

| Workflow requirement | SHA-256 |
|---|---|
| `qwen_image_2.1_Q8_0.gguf` | `cf1962b6c3ffd1d77ab8407f226f72073a333fc82ffe90e2337ad61a4053d47d` |
| `qwen3vl_8b_int8_convrot.safetensors` | `8bfd0f6e12abf2d2d697ecc888e5e90b0d6741d6708f05799f53afa560452e8f` |
| `qwen_image_2.1_vae_bf16.safetensors` | `bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9` |

The existing ACE-Step turbo, 0.6B LM, VAE and embedding checkpoint directories
were inventoried as well: **30 required model/config files** across Qwen and
ACE-Step, no missing files in that inventory. Exact sizes/hashes are in the local
model ledger. Installed files do not prove live model loading, custom-node
availability or memory fit. ComfyUI 0.39.2 and the static-VRAM/split-attention/CPU-VAE
profile are retained prior evidence, not a newly launched service. No model,
custom node, provider or optional ASR/timing dependency was added. Optional
Ollama/ASR/Beat This inference and remote model availability remain unexercised.

## Verification results

| Check | Result / scope |
|---|---|
| Locked install | Passed in the new worktree; extras identical to Windows CI |
| Ruff | Passed (`ruff check .`) |
| Strict Mypy | Passed, 121 source files; strict setting unchanged |
| Character-pack lock | Passed, 48 canonical artifacts; actual production and acceptance PNGs also decoded/hash-checked |
| Native-crash regressions | 31 passed, 51 deselected; mocked providers, including cross-process replay |
| Full relevant offline suite | **1,187 passed**, zero failures/skips; includes durable recovery, render/Studio, Node frontend and publication compatibility (1495.66 seconds including runner overhead) |
| Actual retained format compatibility | All 20 stored storyboard/render manifests parse; 85 illustration profiles recompute to original request fingerprints |
| Environment fingerprint evidence | 40 snapshot rows recomputed; 5 historical rows per database lack a retained set generation fingerprint and remain preserved, independently unproven |
| Disposable historical migration replay | All three database copies pass twice; no historical table contents changed |
| Protected state / installed model preservation | **Unchanged**: all three logical database snapshots and the complete protected file manifest compare equal before/after; all 30 configured model/config files also retain their bytes/hashes. No provider, configuration, selection or publication record changed. |
| Baseline hosted Windows CI | [Run 39162600600](https://github.com/egegoktugbozkir35/ToviTunes/actions/runs/39162600600), SUCCESS on exact `790da5502a966675b94a32838e303a97fb16503e`; 31 native regressions and 1,187 full-suite tests |

The full suite includes durable requests/recovery, render, Studio (including the
Node frontend contract), historical versions, rights/publication and regression
coverage. Test source/fixtures are not edited. Synthetic offline fixtures are
disposable; no live provider request, new production episode or benchmark is run.
Local results concern baseline code; the documentation PR has its own separately
reported GitHub check status. Never substitute the baseline CI run for that PR head.

## Exact reproduction commands

PowerShell from a new task directory; use a new worktree and unique disposable
test paths. Do not point these commands at the production/service environment.

```powershell
git clone --bare https://github.com/egegoktugbozkir35/ToviTunes.git work/ToviTunes.git
git --git-dir=work/ToviTunes.git worktree add -b codex/v2-gate0-baseline work/ToviTunes-v2-gate0 790da5502a966675b94a32838e303a97fb16503e
git -C work/ToviTunes-v2-gate0 rev-parse HEAD
gh pr view 49 --repo egegoktugbozkir35/ToviTunes --json headRefName,headRefOid,baseRefName,isDraft,state,statusCheckRollup
gh api repos/egegoktugbozkir35/ToviTunes/actions/runs/39162600600
# For each exact head in the PR table, test reachability against #49:
git -C work/ToviTunes-v2-gate0 merge-base --is-ancestor f78dcdf1ebb19865552748023b8c9a91bf4829ba 790da5502a966675b94a32838e303a97fb16503e
Set-Location work/ToviTunes-v2-gate0
uv sync --python 3.11 --locked --extra dev --extra video-render --extra web --extra youtube
uv pip freeze
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:PYTHONUTF8 = '1'
uv run --locked ruff check .
uv run --locked mypy src
uv run --locked python -m tovitunes.cli --config config.example.yaml character-pack validate-lock --recipe brands/tovitunes/characters/tovi/packs/v1/intake.yaml --lock brands/tovitunes/characters/tovi/packs/v1/artifact-lock.yaml
uv run --locked pytest -q tests/test_creative_production_v3.py -k native_crash -o faulthandler_timeout=120 --basetemp ../native-crash-test-data --junitxml ../../outputs/validation/native-crash.xml
uv run --locked pytest -q -o faulthandler_timeout=120 --basetemp ../offline-test-data --junitxml ../../outputs/validation/offline-suite.xml --durations=15
```

The executed runner used absolute `--basetemp`/JUnit paths within this task;
`evidence/validation.json` retains the exact argument arrays, working directory,
UTC start, elapsed time and exit status. `uv sync` was run separately before the
runner. Local audit scripts, raw logs, schema/model inventories and before/after
snapshots are delivered outside Git. Their commands, hashes and archive index are
recorded in the local handoff; private databases/media/config contents stay local.

## Known limits / Gate 0 conclusion

The architecture is already functional under existing tests; this is not a claim
that all V2 exit criteria pass. Remaining approved work is stage-aware GPU handoff
and durable source reuse (Gate 1), cross-topic source coherence and safe admission
(Gate 2), human creative/teaching/audio quality (Gate 3), demonstrated compositor/
Studio issues (Gate 4), and the separately authorized frozen batch (Gate 5).
No episode-specific investigation or repair is reopened. The existing single
successful successor is retained as evidence only.

**Gate 0: BLOCKED pending owner approval of the benchmark/budgets and file-ownership
contract.** Green validation alone does not grant PASS or authorize Gate 1/2.
