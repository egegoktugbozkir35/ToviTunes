# Gate 1 — GPU lifecycle and durable execution reliability

**Gate decision: BLOCKED.** The representative existing Qwen workflow's live
memory-fit exit criterion is **unverified pending owner authorization**. This
assignment authorizes zero new generation requests and no paid inference. Passing
offline checks cannot establish a live sampling peak or a full Gate 1 PASS.

Branch: `codex/v2-gate1-reliability`; draft PR target: `codex/v2-gate0-baseline`.
Approved base and verified latest documentation-only head:
`ea3495ae44f2f21018e06db19c90ab49f94c70ca`. The base diff from #49 contains only
`docs/v2-gate0/`. The owner's [Gate 0 approval](https://github.com/egegoktugbozkir35/ToviTunes/pull/50#issuecomment-6270140245)
names that exact head and approves the ownership/accounting contract, with a $0
paid-inference policy. This assignment separately authorizes Gate 1 development.
The locked roadmap and Gate 0 documents remain unchanged.

## General correction

Studio previously started GPU services independently. An uncertain task could
also outlive its worker while local image ingestion had no complete response
receipt. Continuation did not recognize every provenance-retained source, and
recovering an ingest-before-receipt interruption could leave the native task ID
absent from the production image receipt, preventing later admission.

The existing supervisor now coordinates leased GPU boundaries, validates retained
music before handoff, verifies the conservative ComfyUI profile, and stops only
owned handles when work is safely retained. Returned illustration responses are
atomically retained before ingestion. Existing ledgers, provenance, reconciliation
records and selected dependencies drive recovery; no second execution authority,
framework, provider, model, migration or optional dependency was added.

| Criterion | Evidence / disposition |
|---|---|
| Music remains reusable; safe GPU handoff | Offline native-adapter simulation: admitted music, owned ACE-Step shutdown, failed ComfyUI startup, same prepared request on restart, image admission, owned ComfyUI shutdown. Exactly one simulated music release; selected WAV identity unchanged. |
| Owned-process safety | Fake-service lease/ownership/startup/shutdown checks plus a real **CPU-only** Windows process-tree sentinel; an unrelated CPU sentinel survives owned-job shutdown. Busy queues and uncertain durable tasks block automatic shutdown. |
| Returned sources resume without another submission | Interruptions before/after source ingestion, before normalization, after selection, after lease loss, and a second interruption without the response envelope. Native identity, source hash, normalization dependencies and WAV selection remain valid. Environment recovery retains the original request and adds an immutable reconciliation. |
| Ambiguity/native-crash protection | Unknown outcomes remain blocked. Accepted remote IDs cannot be replaced. Native-crash parents cannot transition after abandonment. Existing exceptional authorization, exclusive-service attestations and fresh successor checks remain required. |
| Readiness/progress | GPU startup is deferred. Health reports endpoint reachability only. Missing conservative-profile evidence, startup/stop failures and blocked ownership remain visible; retained stages continue to determine progress. |
| Memory observation without generation | [Sanitized observations](evidence/memory.json): RTX 4070 Laptop, 8,188 MiB total; initial 1,009 MiB used / 6,940 MiB free, later 4,984 MiB used / 2,965 MiB free with external application activity. WDDM per-process memory unavailable. Installed ComfyUI declares both static/low-VRAM flags. No inference service started for either observation; external processes untouched. |
| Preservation | [Evidence](evidence/preservation.json): both read-only snapshots of 2,252 protected paths, 955 registered artifacts and three logical databases match Gate 0 and each other. All 30 configured model/config hashes also match. No authoritative normalization, selection, migration or publication change. |
| Regression / exact-head hosted Windows CI | Focused source, ownership, native-crash, Ruff, strict Mypy and 48-artifact pack checks are required. Full local/offline and exact-head hosted results are recorded in the draft PR and delivery report; an earlier baseline run is not substituted. |
| Representative live Qwen fit | **Unverified pending owner authorization.** Zero new Qwen/ACE-Step/creative generation requests; no paid inference. No live probe is proposed or performed by this delivery. |

The offline scenarios include the existing Blue legacy path and synthetic V3
big/small productions. These are recovery simulations, not the frozen Gate 5
three-category acceptance batch or new editorial/visual-quality evidence.

See [service lifecycle](SERVICE_LIFECYCLE.md) and [sequential shared-file handoff](HANDOFF.md).
The final report pins the delivery head, exact Windows CI run and handoff release.
No episode was repaired, resumed or rendered in authoritative production data.
No PR is merged, no media is published, and no later gate is started.
