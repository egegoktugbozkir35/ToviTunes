# Local generation stack

The production defaults use external localhost services. ToviTunes starts and its
configuration loads when neither service is installed or running. `local-generation
doctor` makes only read-only GET requests and reports `available`, `unavailable`, or
`misconfigured`. It never starts a process or generates media.

## Images: Qwen-Image 2.1 and ComfyUI

All new image generation uses the same local Qwen-Image 2.1 ComfyUI graph,
[`workflows/qwen_image_2_1_t2i_api.json`](../workflows/qwen_image_2_1_t2i_api.json),
at `http://127.0.0.1:8188`. The graph pins `qwen_image_2.1_Q8_0.gguf`,
`qwen3vl_8b_int8_convrot.safetensors`, and
`qwen_image_2.1_vae_bf16.safetensors`. Install and run ComfyUI separately.
ToviTunes does not install, launch, or stop it.

`environment_generation` defaults to 768 × 1376 source PNGs, which the existing
environment pipeline validates and normalizes to 1080 × 1920. Qwen T2I has no
image-reference input. Every environment role repeats the shared textual world
and style contract, with only the staging details changed. No reference artifact
dependency or source-reference claim is recorded for Qwen. The first plate is
used as a reference only with a provider that actually supports reference
images. `lesson_object_generation` defaults to 1024 × 1024 and supplies its
clean-white-background constraint from the lesson-object caller for background
removal. Generated images still require review and rights decisions. Google
Gemini remains selectable for historical or explicit legacy workflows.

## Music: ACE-Step 1.5

ACE-Step is **not installed as part of this PR**. Install it outside the
ToviTunes checkout from the [official ACE-Step 1.5 repository](https://github.com/ace-step/ACE-Step-1.5).
Its own environment/cache downloads the model weights. Do not place weights,
the ACE-Step source, or its Python dependencies in this repository. ToviTunes
uses only the local REST API at `http://127.0.0.1:8001` and does not start or
stop the service. The official project is MIT licensed; each generated audio
candidate still needs separate originality, approval, and commercial-rights
review.

For an approximately 8 GB RTX 4070 Laptop GPU, use the conservative official
profile: 2B turbo DiT (`acestep-v15-turbo`), 0.6B 5Hz LM
(`acestep-5Hz-lm-0.6B`), PyTorch `pt` backend for the detected 6–8 GB tier,
CPU offload, automatic low-VRAM/INT8 handling, and batch size 1. Follow the
ACE-Step GPU detection output if it places the device in a lower tier. Do not
select XL or a 4B LM on this machine. See the [official installation and GPU
guide](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/INSTALL.md)
and [API contract](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/API.md).

The adapter sends authoritative lyrics unchanged, disables `use_format` and
sample mode, and requests a deterministic seed. It uses `/health`,
`/release_task`, `/query_result`, and `/v1/audio` on the configured origin.
The returned task ID is committed before polling. On an uncertain outcome,
`music-benchmark provider-resume` only queries that task; it cannot submit
another one. A queued/running task reports `existing_interaction_pending`
without changing its stored status or task ID; repeat resume later. Downloaded
WAV bytes pass the existing audio inspector and then
the normal ASR, lyric, timing, beat, review, selection, and rights gates.

## Operator steps after merge

1. In a directory **outside ToviTunes**, clone the official ACE-Step repository.
   Follow its current installation guide and run `uv sync` in that checkout.
   This may download dependencies and model weights into ACE-Step's own cache.
2. Configure that ACE-Step environment for the conservative profile above.
   Set `ACESTEP_CONFIG_PATH=acestep-v15-turbo`,
   `ACESTEP_LM_MODEL_PATH=acestep-5Hz-lm-0.6B`,
   `ACESTEP_LM_BACKEND=pt`, and `ACESTEP_OFFLOAD_TO_CPU=true` as appropriate
   for the detected tier. Start its official `uv run acestep-api --host
   127.0.0.1 --port 8001` command from the ACE-Step checkout.
3. In ToviTunes, set `music_generation` as in `config.example.yaml`, then run
   `uv run --locked python -m tovitunes.cli --config config.yaml local-generation doctor`. The music
   status should be `available`. `unavailable` is expected before ACE-Step
   starts; no generation is attempted by the doctor.
4. For the **first live integration smoke generation after merge**, prepare a
   *new future* `MusicBrief` YAML and matching, reviewed `LyricCandidate` YAML.
   Run `uv run --locked python -m tovitunes.cli --config config.yaml music-benchmark run-spec
   --brief-file path/to/new_brief.yaml --lyrics-file
   path/to/new_lyrics.yaml --attempt 1 --confirm-provider-generation`.
   Inspect the request and receipt, run the existing audio/lyric/timing QA,
   and obtain separate human approval and rights clearance before selection.
   If the task is uncertain but has a task ID, use `music-benchmark
   provider-resume --request-id <request-id>`; never retry the release call
   for that request.

No live ACE-Step generation or ComfyUI call is part of this PR or CI.
Historical Colors–Red outputs, render, and publication state are not regenerated.
