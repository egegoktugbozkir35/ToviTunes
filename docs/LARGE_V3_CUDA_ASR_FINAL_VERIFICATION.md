# large-v3 CUDA ASR final verification

The retained benchmark's repeated chorus and missing canonical second line are corroborated by all three ASR models. The CUDA experiment completed through the existing WhisperX pipeline and created immutable analysis version 5 once.

- Repository: `egegoktugbozkir35/ToviTunes`.
- Base SHA: `997a3995ecaa2b42e20b92aa61707e60af8d4d88`, merged PR #21; ancestry verified after `git fetch origin`, clean status and `git rev-parse origin/main`.
- Branch: `codex/cuda-large-v3-asr-verification`.
- Blind ID: `mb_49286b900bcb4dd69f84fd857bbae7ef`.
- Request ID: `451ba192-da15-46d3-b5fd-39712190c838`.
- Audio SHA-256: `613d42c4cbbb133366cde4378585197a82246b3fe7106eb69996466a7e0dd9c0`.
- Evidence date: 2026-09-27.

## CUDA environment

Windows x86-64, Python 3.11.9. The isolated environment is
`C:\Users\Victus\Documents\Codex\2026-09-27\files-pasted-by-the-user-repository-4\work\.venv-cuda`.
The existing validated CPU ASR environment and its package freeze are unchanged.
The new checkout also has a separate minimal `.venv` installed with `uv sync --python 3.11 --extra dev --locked`; it contains neither PyTorch nor WhisperX, and all tests pass there.

The CUDA environment was created with `uv venv --python 3.11`. Non-PyTorch package versions were frozen from the validated CPU ASR environment and installed without version changes. Only torch/torchvision/torchaudio were installed as official CUDA 12.8 builds:

```powershell
uv pip install --python <CUDA_ENV>/Scripts/python.exe torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
uv pip install --python <CUDA_ENV>/Scripts/python.exe -r <CPU_BASELINE_WITHOUT_TORCH_OR_EDITABLE_PROJECT>
uv pip check --python <CUDA_ENV>/Scripts/python.exe
```

For a fresh checkout without the preserved CPU environment, initialize a dedicated environment from the repository lock, then apply the same official wheel override above:

```powershell
$env:UV_PROJECT_ENVIRONMENT = <ABSOLUTE_DEDICATED_CUDA_ENVIRONMENT>
uv sync --python 3.11 --locked --extra dev --extra audio-analysis --extra audio-asr
```

After the override, use the dedicated executable or `uv run --no-sync` with that environment selected. On a fresh workstation, repeat both runtime probes and the selected-model preflight; installed package metadata alone does not validate its GPU.

The baseline excludes `torch`, `torchvision`, `torchaudio`, and editable `tovitunes`; project source is selected explicitly with `PYTHONPATH=<checkout>/src`. The complete resulting package pins are retained in `cuda-environment-freeze.txt`. The normal `uv.lock` and dependency version constraints are unchanged. `uv pip check` passes.

| Component | Installed version |
|---|---|
| torch | 2.8.0+cu128 |
| torchvision | 0.23.0+cu128 |
| torchaudio | 2.8.0+cu128 |
| WhisperX | 3.8.6 |
| Faster-Whisper | 1.2.1 |
| CTranslate2 | 4.8.2 |
| cuDNN used | 9.10.2 (PyTorch API: 91002) |

`nvcc` resolves to system Toolkit 12.9.86. Toolkits 12.9, 13.2 and 13.3 are installed; Toolkit 12.8 is absent. NVIDIA cuDNN 9.25 DLLs for 12.9 and 13.4 are also installed. No system toolkit/driver/cuDNN installer or elevation was needed: the official cu128 wheels supply the requested CUDA 12.8 runtime, cuBLAS 12 and cuDNN 9, and actual execution proves these work together. No source CUDA compilation is performed.

For each dedicated CUDA process, `<CUDA_ENV>/Lib/site-packages/torch/lib` was prepended to process `PATH` and registered with `os.add_dll_directory` before importing the analysis CLI. The directory handle was kept alive. Windows `GetModuleFileNameW` after inference confirmed that `cublas64_12.dll`, `cublasLt64_12.dll`, `cudnn64_9.dll`, `cudnn_ops64_9.dll` and `cudart64_12.dll` were loaded from this environment's `torch/lib`. Global PATH and CUDA environment variables were unchanged.

A reproducible process launcher is:

```python
import os
from pathlib import Path
import sys
lib = Path(sys.executable).resolve().parent.parent / "Lib/site-packages/torch/lib"
os.environ["PATH"] = str(lib) + os.pathsep + os.environ["PATH"]
handle = os.add_dll_directory(str(lib))
from tovitunes.cli import main
raise SystemExit(main(sys.argv[1:]))
```

Run this launcher with the dedicated Python executable and the CLI arguments below. If using `uv run`, set `UV_PROJECT_ENVIRONMENT` to the CUDA environment and use `--no-sync` to preserve the explicitly installed cu128 wheel selection. Do not run an ordinary project sync into this environment after replacing the CPU wheels.

Official references: [PyTorch 2.8 Windows wheels](https://pytorch.org/get-started/previous-versions/#v280), [Faster-Whisper GPU requirements](https://github.com/SYSTRAN/faster-whisper#gpu), [WhisperX installation](https://github.com/m-bain/whisperX#setup).

## GPU and driver

Physical inspection used `nvidia-smi` and `nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used --format=csv`.

| Measurement | Result |
|---|---|
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU |
| Driver | 616.92, WDDM |
| Dedicated VRAM | 8188 MiB / 8,585,216,000 bytes |
| Driver-supported CUDA UMD capability | 13.4 |
| Initial GPU memory use | 1140 MiB; subsequent query 1126 MiB |

Driver CUDA capability is distinct from the verified Python runtime CUDA 12.8. Capability was measured from the physical device, not inferred from the laptop name.

## PyTorch CUDA verification

`torch.__version__=2.8.0+cu128`, `torch.version.cuda=12.8`, `torch.backends.cudnn.version()=91002`, `torch.cuda.is_available()=True`, device count 1, resolved device name `NVIDIA GeForce RTX 4070 Laptop GPU`. An FP16 CUDA matrix multiplication completed successfully. The post-provisioning doctor reports all project assets cached and `offline_ready=true`, with no probe failures.

## CTranslate2 GPU verification

CTranslate2 independently reports one CUDA device and support for `float16`, `float32`, `bfloat16`, `int8`, `int8_float16`, `int8_bfloat16`, and `int8_float32`. A diagnostic loaded the already-cached small.en model on CUDA/FP16 and executed its encoder (shape 1 x 1500 x 768) with downloads/socket connections disabled.

The large-v3 preparation then loaded the actual selected model through WhisperX on CUDA/FP16. A separate, non-persisting cache-only preflight called the existing `transcribe_and_align` on the unchanged MP3; independent transcription and canonical alignment both completed, device `cuda`, batch size 4. It took 10.454406 seconds. This preflight did not allocate analysis version 5. No batch-size reduction, ASR identity change or CPU fallback was necessary.

Doctor output deliberately describes CTranslate2 readiness as device queries only: those checks cannot guarantee DLL availability during inference. The successful encoder, large-v3 load and complete actual pipeline supply that evidence here.

## large-v3 provisioning

Authoritative config:
`C:\Users\Victus\Documents\Codex\2026-09-25\files-pasted-by-the-user-tovitunes-2\work\ToviTunes-live\config.example.yaml`.

Equivalent CLI arguments, passed to the dedicated CUDA launcher:

```text
--config <AUTHORITATIVE_CONFIG> music-benchmark analysis-models prepare --asr-model large-v3 --device cuda --allow-model-download
```

Only large-v3 was newly downloaded. The first download returned an OSError after leaving model files in the project cache; an explicitly authorized retry of the same downloader completed the snapshot. Successful controlled preparation then reused and validated it. Its inventory action is therefore `reused`; the experiment's overall provisioning action was new download followed by reuse. Existing alignment, English punkt_tab and bundled VAD assets were reused.

The official large-v3 snapshot uses `vocabulary.json` and `preprocessor_config.json`, unlike the English-only snapshots' text vocabulary. Cache checks now require these actual large-v3 assets. Inventory reuse checks exact current file paths, including when old paths have disappeared, so hashes from a different cached ASR model cannot be reused.

- Model source: `Systran/faster-whisper-large-v3`.
- Resolved revision: `edaa852ec7e145841d8ffdb056a99866b5f0a478`.
- Cache snapshot:
  `C:\Users\Victus\Documents\Codex\2026-09-25\files-pasted-by-the-user-tovitunes-2\work\ToviTunes-live\data\music-benchmark\.analysis-models\asr\models--Systran--faster-whisper-large-v3\snapshots\edaa852ec7e145841d8ffdb056a99866b5f0a478`.
- Inventory: `<retained data>/music-benchmark/.analysis-models/inventory.json`; copy retained as `large-v3-preparation.json`.
- Offline model load validation: true.

| ASR asset | Bytes | SHA-256 |
|---|---:|---|
| model.bin | 3087284237 | `69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1` |
| config.json | 2394 | `a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9` |
| tokenizer.json | 2480617 | `6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca` |
| vocabulary.json | 1068114 | `c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1` |
| preprocessor_config.json | 340 | `7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711` |

The inventory also records alignment weight SHA-256 `488fd4f16de84438ffc945334278c1b9fb9b7159a806c1080b16111a958c945d`, NLTK hashes and the bundled VAD hash. Unknown upstream alignment/VAD revisions remain null.

## offline/cache-only proof

The single authoritative run used `tovitunes.cli.main` with the existing `music-benchmark analyze-audio` command, not a standalone Faster-Whisper persistence path:

```text
--config <AUTHORITATIVE_CONFIG> music-benchmark analyze-audio --blind-id mb_49286b900bcb4dd69f84fd857bbae7ef --analysis-version 5 --device cuda --asr-model large-v3
```

`--allow-model-download` was absent; `AnalysisConfig.allow_model_download=false` is persisted. The selected snapshot came from the project cache with `local_files_only=true`. `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` were enforced, and an outer `model_environment(..., False)` blocked outbound `socket.connect`, `connect_ex` and `create_connection` for the entire authoritative CLI call. The existing inner pipeline guard supplied the same protection during inference/alignment. Generation and provider-resume entry points were patched to fail if invoked, with zero invocations.

The actual path remained **WhisperX -> Faster-Whisper/CTranslate2 -> independent recognition -> WhisperX alignment**. No canonical lyrics were supplied as an ASR prompt. English was explicit, diarization false, CUDA FP16 and batch size 4 were recorded in transcription provenance. The completed row's revision matches the provisioned snapshot. The CLI reported `reused=false`, `transcription_status=complete`, `qa_status=fail`.

Analysis JSON SHA-256: `34d8e5e1803f00f7b53727a1637522b9dbef8f5ca01a3d48b0ec11068dff6006`.

## version 3 small.en

Exact persisted transcript:

```text
Red, red, look ahead A red, apple, round and bright A red, ball rolls into sight Red, red, what do you see? Red is a colour, sing with me Red, what do you see? Red is a color, sing with me. Red, red, what do you see? Red is a color, sing with me.
```

| Measurement | Value |
|---|---|
| Device / compute type | cpu / int8 |
| Recognized words | 54 |
| Canonical words | 36 |
| Matches | 30 |
| Substitutions | 0 |
| Insertions | 24 |
| Deletions | 6 |
| WER | 0.8333333333333334 |
| Coverage | 0.8333333333333334 |
| Independent aligned words | 54 |
| Mean independent CTC score | 0.6870555555555555 |
| First recognized timestamp (seconds) | 2.254 |
| Last recognized timestamp (seconds) | 50.544 |
| Canonical aligned words | 36 |
| Canonical aligned lines | 7 |
| Canonical minimum alignment score | 0.294 |
| Transcription / canonical alignment status | complete / complete |

Required phrase presence: `red` = true, `color` = true, `red apple` = true, `red ball` = true, `red is a color` = true.

## version 4 medium.en

Exact persisted transcript:

```text
Red, red, look ahead A red apple, round and bright A red ball rolls into sight Red, red, what do you see? Red is a color, sing with me Red, what do you see? Red is a color, sing with me. Red, red, what do you see? Red is a color, sing with me.
```

| Measurement | Value |
|---|---|
| Device / compute type | cpu / int8 |
| Recognized words | 54 |
| Canonical words | 36 |
| Matches | 30 |
| Substitutions | 0 |
| Insertions | 24 |
| Deletions | 6 |
| WER | 0.8333333333333334 |
| Coverage | 0.8333333333333334 |
| Independent aligned words | 54 |
| Mean independent CTC score | 0.6912037037037037 |
| First recognized timestamp (seconds) | 2.254 |
| Last recognized timestamp (seconds) | 50.544 |
| Canonical aligned words | 36 |
| Canonical aligned lines | 7 |
| Canonical minimum alignment score | 0.294 |
| Transcription / canonical alignment status | complete / complete |

Required phrase presence: `red` = true, `color` = true, `red apple` = true, `red ball` = true, `red is a color` = true.

## version 5 large-v3

Exact persisted transcript:

```text
Red, red, look ahead A red apple, round and bright A red ball rolls into sight Red, red, what do you see? Red is a color, sing with me Red, what do you see? Red is a color, sing with me Red, red, what do you see? Red is a color, sing with me
```

| Measurement | Value |
|---|---|
| Device / compute type | cuda / float16 |
| Recognized words | 54 |
| Canonical words | 36 |
| Matches | 30 |
| Substitutions | 0 |
| Insertions | 24 |
| Deletions | 6 |
| WER | 0.8333333333333334 |
| Coverage | 0.8333333333333334 |
| Independent aligned words | 54 |
| Mean independent CTC score | 0.6903148148148148 |
| First recognized timestamp (seconds) | 2.254 |
| Last recognized timestamp (seconds) | 50.484 |
| Canonical aligned words | 36 |
| Canonical aligned lines | 7 |
| Canonical minimum alignment score | 0.294 |
| Transcription / canonical alignment status | complete / complete |

Required phrase presence: `red` = true, `color` = true, `red apple` = true, `red ball` = true, `red is a color` = true.

## transcript structural comparison

Canonical sequence:

```text
Red, red, look ahead!
Red is a color, yes, red!
A red apple, round and bright.
A red ball rolls into sight.
Red, red, what do you see?
Red is a color, sing with me!
Red!
```

All three independently recognize the following structure. Version 5's words are shown; version 3 uses `colour` in the first answer and differs in punctuation.

1. `Red, red, look ahead`
2. `A red apple, round and bright`
3. `A red ball rolls into sight`
4. `Red, red, what do you see?`
5. `Red is a color, sing with me`
6. `Red, what do you see?`
7. `Red is a color, sing with me`
8. `Red, red, what do you see?`
9. `Red is a color, sing with me`

| Structure, punctuation ignored | v3 small.en | v4 medium.en | v5 large-v3 | Canonical |
|---|---:|---:|---:|---:|
| Full `Red, red, what do you see?` | 2 | 2 | 2 | 1 |
| Shortened `Red, what do you see?` | 1 | 1 | 1 | 0 |
| `Red is a color, sing with me!` | 2 + 1 `colour` | 3 | 3 | 1 |
| `Red is a color, yes, red!` | 0 | 0 | 0 | 1 |
| Separate ending `Red!` recognized | no | no | no | yes |
| WER | 0.8333333333333334 | 0.8333333333333334 | 0.8333333333333334 | — |
| Coverage | 0.8333333333333334 | 0.8333333333333334 | 0.8333333333333334 | — |

v5 and v4 have identical normalized token order. WER/coverage deltas are zero. The v5 last independently aligned word ends 0.060 seconds earlier; its mean CTC score differs from v4 by approximately -0.000889. The repeated structure is assessed directly from the transcripts, not deduced from WER.

## lyric deviation evidence

Large-v3 corroborates two full question lines, one shortened question and three answer lines, exceeding the canonical one question/answer cycle. It moves directly from the opening to the apple line and never recognizes the canonical `Red is a color, yes, red!` line. It does not restore the intended canonical sequence.

The three ASR models agree on the retained recording's repeated chorus and omitted canonical second line. This is evidence for this one artifact, not a universal claim about Lyria. They share the WhisperX frontend/VAD and alignment model, so agreement is not proof of statistical independence across entire pipelines.

CTC scores measure alignment fit, not calibrated recognition confidence; recognition confidence remains null. Canonical forced alignment is given canonical text, so its 36 words/7 lines do not establish that the omitted line was sung. Its minimum score 0.294 remains below the unchanged 0.5 admission threshold. The edit-distance algorithm can match the expected final `red` inside a repeated chorus; the absent standalone ending is therefore determined from recognized structure rather than deletion counts alone. No timestamps, confidence or downbeats were invented.

## QA/timing state

Duration is **58.01795918367347 seconds**, above the unchanged **45-second maximum**. Overall QA remains **fail**, independently of ASR interpretation. Version-5 QA policy (`music_qa`, v2) also fails lyric adherence, teaching intelligibility, educational correctness, preschool safety and production fit; beat usability passes and broad artifact absence remains unknown.

Timing policy remains **fail**. Downbeat count is 0. No intro/outro boundaries are inferred. Canonical alignment admission remains controlled by the unchanged coverage/WER/minimum-score thresholds, so exported timing words, lyric lines and sections remain empty. The version-5 timing record is pending; no timing approval decision was added.

Rights remain **unknown**, music approval **pending**. No rights confirmation or song approval occurred. Existing thresholds and policies were unchanged.

## performance

| Operational measurement | Result |
|---|---|
| GPU / dedicated VRAM | RTX 4070 Laptop GPU / 8188 MiB |
| large-v3 WhisperX CUDA model load | success |
| Compute type / batch size | float16 / 4 (unchanged) |
| Analysis wall-clock runtime | 27.347747 seconds |
| Audio duration | 58.01795918367347 seconds |
| Approximate real-time factor | 0.471367 |
| Peak allocated PyTorch VRAM | 1157075968 bytes (1103.47 MiB) |
| Peak reserved PyTorch VRAM | 1535115264 bytes (1464.00 MiB) |

The timer covers the authoritative CLI workload after wrapper imports, including local measurements, ASR, alignment and persistence/QA evaluation; it excludes environment setup/downloads and the separate preflight. Real-time factor is workload seconds divided by audio seconds. PyTorch allocator measurements exclude CTranslate2 allocations and other GPU processes, so these are not whole-process peak GPU memory. Performance is operational evidence and does not affect QA policy.

## safety audit

| Constraint | Result |
|---|---|
| Lyria generation POSTs | 0 |
| Provider-resume GETs | 0 |
| Songs generated | 0 |
| Google generation calls | 0 |
| Original MP3 modifications | 0 |
| Original MP3 SHA unchanged | yes; `613d42c4cbbb133366cde4378585197a82246b3fe7106eb69996466a7e0dd9c0` |
| Versions 3 and 4 unchanged | yes; complete row hashes and timing row hashes match the pre-run snapshot |
| All earlier analyses/timing versions unchanged | yes, including versions 1 and 2 |
| Version 5 newly created exactly once | yes; absent before, one new primary-keyed analysis row and one new timing row after; CLI `reused=false` |
| Music requests/receipts/outputs/reviews unchanged | yes; full-table row digests and counts match |
| Rights/approval/lyric/timing decisions unchanged | yes; full-table row digests and counts match |
| Rights | unknown, unchanged |
| Approval | pending, unchanged |
| Audio processing | decode/read for inference only; no retained-file rewrite, trim, transcode, normalization or regeneration |

Generation/provider-resume runtime guards and unchanged database table counts/digests support the zero-call audit. No generation command or provider-resume command was invoked in setup, provisioning or evidence collection. Model downloads were public model provisioning only. The sole authoritative analysis command allocated version 5 after successful preflight, and it was not rerun.

## validation

After the final code changes and before successful controlled model provisioning: `uv run pytest -q` **264 passed**, `uv run ruff check .` passed, `uv run mypy src` passed for 41 source files, and `git diff --check` passed. Tovi character-lock validation passed with **48 artifacts**, including a repeat after authoritative analysis. CUDA environment package compatibility passes; preserved CPU doctor is cache-ready, and its package freeze matches the initial freeze. Minimal installation and CI run without NVIDIA hardware or model downloads; model-related tests mock loaders/devices and forbid outbound calls. CI configuration and `uv.lock` are unchanged.

Repository changes are limited to controlled large-v3 preparation/cache formats, stronger CUDA diagnostics, ASR inventory path validation, mocked tests, the optional-import mypy override and documentation. No batch configuration change was needed. No models, DLLs, environments, caches, MP3, database or generated analysis JSON are committed.

Supporting local deliverables: `analysis-version3.json`, `analysis-version4.json`, `analysis-version5.json`, `large-v3-preparation.json`, `cuda-runtime-preflight.json`, `cuda-doctor-before-provisioning.json`, `cuda-doctor-after-provisioning.json`, `large-v3-inference-preflight.json`, `large-v3-authoritative-run.json`, `cuda-analysis-performance.json`, `qa-version5.json`, `timing-version5.json`, `integrity-before.json`, `integrity-after.json`, `preserved-cpu-doctor.json`, and `cuda-environment-freeze.txt` alongside this report in task outputs. The report is also included in repository docs; supporting runtime assets/data remain local.

## final classification

**CONFIRMED_GENERATION_LYRIC_DEVIATION**
