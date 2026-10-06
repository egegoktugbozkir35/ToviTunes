# Local WebUI and YouTube private tests, V1

Studio V1 supersedes the original frontend described below. For the current Dashboard /
Generate / Videos / Settings workflow, durable jobs and automatic local startup, see
[Studio V1](STUDIO_V1.md). The historical endpoints and publication gates remain supported.

## Install and start

Run `uv sync --python 3.11 --locked --extra web --extra youtube --extra video-render` and then `start-ui.bat` from the repository root on Windows. The Python invocation is `uv run python -m tovitunes.web --config config.yaml`. The app binds `127.0.0.1:8766` and prints its local URL, config path, and database path. The browser requires the existing production database and data root to show episode evidence. The sample configuration alone starts an empty local studio.

## Architecture and routes

The FastAPI server is a control surface. `RuntimeConfig`, `Database`, `AssetStore`, selections, approvals, rights, leases, render manifests, and `ProductionRenderer` remain authoritative. `JobManager` holds only ephemeral progress and runs one heavy operation at a time. Meaningful results and YouTube attempts are persisted by the existing services or the narrow `0016_publication_attempts.sql` migration. No browser handler modifies SQLite directly.

Routes: `GET /api/health`, `/api/system`, `/api/episodes`, `/api/episodes/{key}`, `/api/jobs`, `/api/jobs/{id}`, `/api/episodes/{key}/release-preflight`, `/api/youtube/status`, `/api/episodes/{key}/youtube/video-status`, and `/api/media/{artifact_id}`. Operator actions are `POST /api/episodes/{key}/render`, `/api/youtube/connect`, and `/api/episodes/{key}/youtube/upload-private`. The media route serves only registered, SHA-verified, expected-MIME artifacts under the trusted data root. The server checks localhost hostnames and rejects cross-origin writes; broad CORS is not enabled.

Creative eligibility is shown read-only. A future orchestration PR can call `CreativeWorkflow.generate_next()` and connect its durable output to music, storyboard, render, review, and publication. This V1 does not offer `Generate Next Short`.

The donor `ollama-mpt-youtube` informed the single-worker job model, installed-app OAuth refresh behavior, resumable retry boundary, and safe failure reporting. This repository has its own code, database, config, and secrets. No donor import or checkout is needed at runtime.

## Release gate and rights policy

`evaluate_release(config, episode_key)` is a provider-free Python service. It checks selected final render, immutable SHA/MIME, current media QA, manifest binding and dependencies, approved character pack, selected environment and lesson-object evidence when required, current artifact approvals, and each dependency's separate rights decision. Every check reports scope, reason, and artifact ID when applicable.

`render_ready` means technical evidence and approval pass. `private_test_upload_allowed` additionally needs selected, validated publication metadata bound to the render and no `blocked` or missing rights status in the publication graph. `unknown` and `review_required` rights can permit a deliberately requested **private test**. `public_release_allowed` requires every required dependency to be `commercial_use_confirmed`; visual approval never grants rights. This V1 has no public or unlisted upload route. A private test leaves all rights records untouched and is not release clearance.

Publication metadata comes from the selected `publication_metadata` artifact and is validated with `EpisodePublicationMetadata`. The uploader never invents a title or description. If the Colors–Red pilot lacks selected reviewed metadata, the UI reports this blocker. Its artifact SHA, manifest, QA and rights determine the other results from the current database; no pilot-specific result is hardcoded.

## OAuth and channel identity

Configure `publication.youtube.enabled`, `expected_youtube_channel_id`, local `credentials_file`, and local `token_file` in `config.yaml`. Relative paths resolve beside that file. Both OAuth files belong under ignored `secrets/`. Use an installed-app OAuth client with `https://www.googleapis.com/auth/youtube.upload` for upload and `https://www.googleapis.com/auth/youtube.readonly` for channel identity and private video status. An old upload-only token needs an explicit reconnect. `Connect YouTube` may open the system browser for authorization. A valid saved token is reused; expired credentials with refresh tokens are refreshed. Only structured `invalid_grant` permits a fresh interactive authorization. Transient refresh errors stop without launching a browser. Tokens are persisted atomically and their contents never enter API responses or logs.

After OAuth, `channels.list(part="id,snippet", mine=True)` identifies the channel. The UI shows its title and ID beside the expected ID. A mismatch blocks upload before any attempt. The private upload body always sets `privacyStatus=private` and `selfDeclaredMadeForKids=true`. `containsSyntheticMedia` comes from explicit ToviTunes config and defaults to true. The category ID is configurable; title, description and tags are drawn from selected metadata.

## Durable attempts and uncertain remote outcomes

Each private attempt stores its episode, render artifact ID/SHA, metadata fingerprint, timestamps, mode, outcome, privacy, safe error classification, and exact YouTube video ID when known. Outcomes are `prepared`, `remote_started`, `succeeded`, `terminal_failure`, and `ambiguous`. An episode lease is acquired before publication. `remote_started` is committed immediately before the first `next_chunk()` call. Retries are bounded and stay inside that same resumable upload request; only transient transport errors and HTTP 429/500/502/503/504 are retried. Explicit upload-limit rejection is terminal. Uncertain failure after remote start becomes `ambiguous`, and the UI says **Manual reconciliation required**. It never starts a new upload after such an attempt automatically. A successful attempt with the same episode, render SHA, and metadata fingerprint is returned instead of uploaded again. The watch URL is only a presentation link; the video ID is the remote identity.

The read-only `videos.list` action manually refreshes processing, privacy, title, and made-for-kids status for a recorded successful video. V1 has no scheduled publication, public publishing, analytics, thumbnail creation, or cross-process job queue. Keep the server local; remote hosting requires a separate authentication design.
