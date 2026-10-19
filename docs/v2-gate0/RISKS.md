# Minimal V2 risk register

Only risks within Locked Roadmap v1.0 are listed. Existing safeguards describe
current code/evidence, not a claim that the later gate has passed.

| Risk | Existing safeguard / current limit | Responsible approved gate |
|---|---|---|
| GPU resource contention / OOM | Owned-process supervision, endpoint reuse, CPU VAE/split attention and retained conservative static-VRAM launch evidence; admitted music and immutable receipts can be reused. Automatic stage-aware ACE-Step/ComfyUI handoff is not established. Never run both generation processes to accelerate testing. | Gate 1; stop there if representative approved workflow cannot fit current hardware. |
| Ambiguous provider outcome / duplicate submission | Durable remote-start/native IDs, lease fencing, no blind image/music resend, immutable parent/successor evidence and fresh native-crash rechecks. External clients are outside the application lease and require exclusive service control. | Gate 1; Gate 5 verifies whole-batch safety. |
| Source-art inconsistency / unsafe alpha admission | Versioned episode-world and subject prompts, source retention, safe extraction refusal, hashes/receipts and source-to-composite attribution. Text-only Qwen and technical PNG checks cannot certify semantics or coherent style. | Gate 2; Gate 3 planning consumes its frozen contracts; Gate 4 cannot conceal bad sources. |
| Historical compatibility regression | Immutable artifact/version/dependency pins, checksum-verified migrations, retained parsers/fixtures, no rewriting old provider contracts; disposable migration replay and existing offline suite. | Each owning gate preserves its formats; Gate 5 runs final integrated regressions. |
| Cross-branch conflicts / duplicated stack integration | Exact Git ancestry proves #45/#46 already included; isolated branches, whole-file exclusive ownership and explicit commit handoffs. Shared filenames cannot be modified concurrently even in different functions. | Gate 0 freezes ownership; Gates 1/2 follow handoffs; Gate 5 verifies integration once. |
| Insufficient provider availability | Existing bounded configured creative chain, HTTP 410 handling, optional disabled-by-default Ollama path, local prerequisites and truthful readiness. Installed files/advertisement are not generation evidence. No new provider/model or extra batch follows from failure. | Gate 1 for service feasibility; Gate 3 for existing creative/music policy; Gate 5 for approved batch readiness/stop decision. |
| Technical success without editorial acceptance | Technical QA remains distinct from source-art, teaching and human listening review. Locked 3/3 conditions, complete native/mobile playback, recorded human evidence and owner editorial approval; publication stays separately gated. | Gates 2/3/4 improve their existing areas; Gate 5 alone records release-candidate decision. |

No risk authorizes new infrastructure, model evaluation, automatic retry, an extra
acceptance run or a seventh workstream.
