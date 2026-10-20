# Gate 1 service lifecycle

## Admission and transitions

1. Opening Studio performs endpoint/status inspection and may warm up configured
   Ollama helpers. It does not eagerly start ACE-Step and ComfyUI together.
2. The existing global execution lease controls production. A native music
   operation lazily resolves the supervisor already attached to the same runtime
   configuration/database, or creates one for CLI execution. Injected providers
   retain their caller-managed lifecycle for offline tests and explicit adapters.
3. Completed music is reconciled from the existing receipt/binding; it does not
   start a new provider operation. The WAV is retained and selected as before.
   The existing AudioClock or historical source-admission path must also pass.
4. Immediately before a missing illustration is submitted, the orchestrator
   recomputes read-only music admission under the lease. Only then may the
   supervisor stop owned ACE-Step resources and prepare the requested ComfyUI
   endpoint. The same check applies when environments need generation.
5. Existing healthy instances are reused. An external ACE-Step/other ComfyUI
   endpoint blocks a conflicting GPU transition; the application never kills it.
   A reused ComfyUI instance must expose the conservative profile in its argv.
6. Every owned stop checks durable uncertain work. ComfyUI must also have an
   observed empty running/pending queue. Missing observations block shutdown.
   An uncertain task without returned evidence keeps its retrieval service alive,
   including during application shutdown. Inspect/recover the retained task;
   do not infer that an empty history authorizes a resend.
7. After all visual sources are admitted, owned ComfyUI resources are released
   before CPU storyboard/render work. This boundary is rechecked on continuation.
   A shutdown failure blocks progress and retains process handles and logs.

No PID discovery or process-name matching grants ownership. On Windows an inert
Python bootstrap is assigned to a private OS Job Object before it may launch the
service. Descendants inherit that process boundary; shutdown uses its retained
handle, not `taskkill` or a PID lookup. The unrelated sentinel regression verifies
isolation. This OS process grouping is not a production/job ledger. It adds no
scheduler and owns no request state. A restarted application has no old handles:
surviving endpoints are external and are never automatically reclaimed or killed.
The handle/inheritance behavior follows Microsoft's [Job Object contract](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject)
and [termination contract](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-terminatejobobject).

Startup/readiness/stop operations are serialized within the supervisor. Startup
waits are bounded by existing configuration; stop waits are bounded. Failed
startup does not consume a prepared illustration request. Failures stay visible
until a deliberate retry/recovery verifies them. Logs and child environments
retain the existing credential-redaction boundary; Windows children are hidden.

## Repeatable conservative launch

Use the existing executable/cwd configuration or the existing
`TOVITUNES_COMFYUI_HOME` override pointing at the installed ComfyUI root. Automatic
discovery reads `comfy/cli_args.py` without executing the service. Where both are
declared it adds:

```text
--disable-dynamic-vram --lowvram
```

The installed Desktop discovery path retains its existing
`--use-split-cross-attention --cpu-vae` and shared model mapping. An explicit
configured command must include the conservative flags itself; it is not silently
rewritten. Before image submission, a read-only `/system_stats` check verifies both
flags on the actual service. An unsupported/unverifiable profile blocks the stage
with an operator-visible reason. It does not change dimensions, steps, sampler,
seed, workflow, prompt, model, normalization algorithm or generation parameters.

Endpoint health and an idle-memory snapshot cannot prove a representative Qwen
sampling run fits VRAM. The existing 8 GB hardware exit requirement remains
unverified until a separately authorized, precisely counted live probe succeeds.

## Durable source continuation

Illustration response evidence is one fsynced, atomically replaced JSON envelope
under the existing production working root, keyed by its validated ledger UUID.
It includes bytes, SHA-256, immutable input/provider/model/task identities and
allowlisted response metadata. It is neither polled as a queue nor used to grant
submission authority. Incomplete temporary files grant no retry permission.

On Resume, the system validates the envelope or discovers an already ingested raw
artifact by exact request provenance. It then runs the existing normalization and
admission code locally. A selected-before-receipt interruption repairs the same
source/native/normalized receipt and dependency graph. Lease loss may retain
returned evidence, but only a valid lease may admit it. Corruption or identity
mismatch blocks continuation without another provider submission.

Environment recovery uses the existing immutable reconciliation table. A retained,
hash-validated source bound to the original accepted native task permits local
normalization after interruption. Repeated invocation reuses the same raw and
normalized artifacts and the same reconciliation. The original request row stays
unchanged. A definitive prior processing failure still follows explicit operator
reconciliation; an unknown remote outcome without source evidence stays blocked.

Native-crash abandonment remains exceptional. Its original ambiguous request is
immutable, the owner must attest the existing evidence/exclusive-service conditions,
and the prepared successor must pass fresh checks before its one distinct POST.
Neither shutdown, restart nor a successful source on another request grants a retry.
