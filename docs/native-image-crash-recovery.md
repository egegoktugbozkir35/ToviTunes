# Local ComfyUI native crash abandonment

`production retry-image-request` still rejects ambiguous requests. A native abort
can lose ComfyUI's in-memory terminal history. It must not be relabeled a confirmed
provider failure. The exceptional command below preserves the ambiguous row and
its task ID, and prepares one distinct successor without submitting either task.

Use only for local Qwen CUDA split-attention OOM followed by a recorded native
`Fatal Python error: Aborted`. An operator must verify that the log belongs to the
specified task, the original process exited, both old and current services use
their standard output/temp directories, and no other client uses that service
through authorization and the later submission. These are operator attestations,
not inferred terminal history. A timeout or absent history alone is insufficient.

Supply a UTF-8 JSON evidence file with these fields:

```json
{
  "request_id": "original local request UUID",
  "episode_id": "existing episode UUID",
  "provider_request_id": "original ComfyUI prompt_id",
  "input_fingerprint": "original lowercase SHA-256",
  "comfyui_root": "absolute path to the original ComfyUI installation",
  "crash_log_path": "absolute path to retained native crash log",
  "crash_log_sha256": "SHA-256 of those original log bytes",
  "confirm_log_attribution_and_exclusive_service": true,
  "confirm_original_process_exited": true,
  "confirm_no_output_directory_overrides": true
}
```

Start only ComfyUI at the configured localhost URL, using an absolute `main.py`
path and the same model paths, workflow, dimensions, sampler and CPU VAE. Inspect
the installed `main.py --help` first. Where supported,
`--disable-dynamic-vram --lowvram` disables dynamic loading and enables estimated
offloading; lowvram alone has no effect with dynamic VRAM. This verifies startup,
not that sampling fits. For an episode with admitted music, keep ACE-Step stopped.

After separate operator authorization:

```text
python -m tovitunes.cli --config ISOLATED_CONFIG production abandon-native-image-task --request-id ORIGINAL_REQUEST --evidence EVIDENCE_JSON --confirm-abandon-native-crash --actor OPERATOR --reason "Abandon evidenced local native crash; authorize one distinct successor"
```

The command holds the production execution lease and one SQLite transaction. It
checks identity against the original receipt fingerprint, retained receipts,
provenance and selections, and rejects newer requests. Fresh GET-only checks must
show the expected ComfyUI installation, an entirely empty queue, and absent
original task history. It scans native output/temp and application data for image
files modified at or after the original request. Any possible output, unavailable
probe, directory override, or missing directory blocks authorization. This is
deliberately conservative; unrelated recent images can also block it. Inspect and
recover retained work rather than deleting evidence to pass the check.

The existing immutable `image_retry_authorizations` table enforces one successor
per parent. `image_native_crash_abandonments` retains the supplied attestations,
log text/hash and fresh observations, with update/delete forbidden. Repeated
authorization returns the same successor, including after restart. It does not
revalidate an already granted authorization or create a second request. Immediately
before the later provider POST, the image stage verifies the immutable grant,
successor/parent identities, retained work and fresh live provider state again.
It rechecks ownership afterward, then atomically moves the successor out of
`prepared` before POST. A failed fresh check leaves it prepared and blocked.
Queue/history are checked again after file inspection; changed observations or
checks taking over 30 seconds are rejected. A prepared grant is never a cached
permission to submit using the original observations.

The application lease serializes compliant ToviTunes workers, not arbitrary
external ComfyUI clients. The final GETs and POST cannot be one atomic ComfyUI
transaction. Exclusive local-service control remains an operator prerequisite.

The successor copies episode/run ownership, slot, provider/model, fingerprint and
original spec/profile, links its parent, and has no native task ID until a later
submission. Existing input checks preserve the pinned plan and dependencies. The
original request and receipt are untouched. Music is reused by the normal retained
audio path. Authorization does not resume production. A later production resume
can generate additional missing assets and requires its own appropriate scope.
