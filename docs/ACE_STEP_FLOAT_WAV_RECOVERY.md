# ACE-Step IEEE-float WAV compatibility and explicit recovery

Python 3.11's `wave` reader supports integer PCM only ([Python documentation](https://docs.python.org/3.11/library/wave.html)). A complete RIFF/WAVE response with format tag 3 can therefore be valid IEEE-float audio while the old inspection path rejects it. The application stays on the locked Python 3.11 environment and existing `miniaudio` dependency.

## Original audio admission

One inspector now handles the ACE-Step response, benchmark WAV helpers, receipt verification and production handoff. It validates the complete declared RIFF size, all chunk bounds and padding, a single format/data pair, channel count, sample width/rate, byte rate, block alignment and integral frame count. A `fact` frame count, if present, must match. Supported uncompressed formats are integer PCM (8/16/24/32 bits) and IEEE float (32/64 bits), including matching extensible PCM/float subformats. Unsupported encodings, missing/truncated data, inconsistent metadata and nonfinite float samples fail closed.

`miniaudio` decodes all frames. Its source metadata and decoded frame count, channels, rate and duration must agree with the container. Float source codecs are `pcm_f32le` / `pcm_f64le`; they are never labelled as integer PCM. Receipt metadata retains the original sample format, frame/channel count and rate. Original response bytes, byte count and SHA-256 are preserved in staging, the immutable receipt, final audio file and episode audio artifact. Decoding to an in-memory float view for measurements does not replace/transcode that file.

The downstream audit found the second integer-only reader in `music.benchmark.inspect_wav_metadata`; it now delegates to the same inspector. Audio measurements already use `miniaudio` float decoding and now cross-check the verified sample shape. Rhythm uses the decoded samples, ASR and Renderer V4 use the retained file through FFmpeg, and production acceptance re-verifies the original receipt codec/SHA. The end-to-end regression exercises actual stereo float decoding, analysis and local MP4 rendering with offline transports/model fixtures.

## Previously rejected request

The reported request is `6f4595ff-2ebd-4ac2-99f1-4d2332725824`, with a retained ACE-Step task, terminal failure reason `ACE-Step returned invalid WAV audio`, and no receipt. A normal Render resume and `music-benchmark provider-resume` still block this terminal failure. Recovery requires an explicit operator command against the **active production configuration/database**:

```powershell
uv run --locked python -m tovitunes.cli --config <active-config.yaml> music-benchmark reconcile-ace-step-float-wav --request-id 6f4595ff-2ebd-4ac2-99f1-4d2332725824 --actor <operator-name> --rationale "Verified IEEE-float WAV compatibility rejection during Render acceptance"
```

Replace the two angle-bracket placeholders before running. Use the existing configuration and original ACE-Step endpoint. The command acquires the singleton production lease, so it cannot run concurrently with another production worker. It applies the new additive migration through normal migration handling; historical migrations are not rewritten.

Admission is restricted to `ace_step_local` / `acestep-v15-turbo`, the exact invalid-WAV rejection from the provider/retrieval path, an existing remote-start marker/task ID, no existing unaudited receipt, an unchanged canonical fingerprint/translated request, and the episode's pinned endpoint. It calls only `/query_result` for the retained task and `/v1/audio` for that task's validated result. It never calls `/release_task`. Task status/identity, output count/model/lyrics, same-origin audio URL and complete audio decoding retain the existing fail-closed checks. The returned audio must actually be IEEE float; an integer replacement, corrupt/truncated payload or unrelated terminal failure does not qualify.

After validation, the returned original bytes are durably staged. One SQLite transaction inserts the immutable receipt and `music_wav_reconciliations` audit record, including the **entire original failed request row**, actor, rationale, retained task ID, audio SHA and reconciliation ID. The request must still equal that captured row at commit. Audit failure rolls back the receipt; verified staging can be reused without overwriting bytes. Audit records cannot be updated, deleted or replaced.

Only then can existing receipt/output finalization mark the same request `succeeded`. Its request ID, task ID, canonical inputs, episode binding, remote-start/creation markers and original failure category/reason are retained; the audit also preserves the original terminal status and timestamps. No new song request or audio candidate is submitted. Rights, lyric/QA/timing and publication policy gates remain independent. If ownership is lost after the receipt/audit commit, those facts survive and a later explicit reconciliation finalizes them without any provider query. Repeating a completed reconciliation reuses that same receipt/output and returns the same reconciliation ID.

If the task remains unavailable/pending, its output is malformed or the stored contract/endpoint differs, the command leaves the failure blocked. Do not replace the task identity, clear its failure or generate another song to bypass those checks.

## Continue Render afterward

After the command returns `status: succeeded`, run the updated application and Recover the failed Render job, or Resume/Render the same existing episode. Do not use Studio Create/Generate for this continuation. Music uses the retained receipt without querying or submitting ACE-Step again; Render proceeds through the normal audio analysis, visual and rendering gates. The command itself performs no YouTube operation and does not publish anything.

The automated regressions use temporary databases and mocked provider transports only. Implementing and validating this recovery path does not claim that the real-machine database/request has already been reconciled; the explicit command above is the live operator action.
