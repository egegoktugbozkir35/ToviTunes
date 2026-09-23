# Continuation and dry planning

The planner reads selected artifact versions, current review and rights decisions, file hashes, dependency IDs, and unresolved generation requests. It returns one next action: `produce`, `select`, `review`, `repair`, `stale`, `reconcile`, `rights`, or `complete`. Planning does not invoke a provider, change a selection, or mark a request successful. A future runner must acquire an episode lease and recheck the plan and owner token immediately before each side effect.

The pinned learning objective needs an episode-level approval before the static path: `EpisodeSpec → lyrics → MusicSpec → approved audio master → alignment and beat analysis → TimedStoryboard`. Scene image and character-animation slots are then derived from the selected storyboard index. Each downstream artifact must pin the exact selected prerequisites. A missing scene produces one scene-slot action; an accepted song and earlier scenes remain selected. Replacing the audio master makes its alignment and storyboard descendants stale rather than silently regenerating the song.

The storyboard index is a deliberately small Phase 3 contract: JSON with `schema_version: 1`, `audio_master_artifact_id`, and nonempty unique `scene_ids`. Exact timestamps, lyric cues, gestures and educational fields arrive in the timed-storyboard phase. An invalid index or one pointing to a different audio master is held for repair.

`RequestLedger` records expensive request identity and distinguishes prepared, remote-started, succeeded, failed and ambiguous outcomes. An unresolved or completed request for a slot prevents a second implicit request. Definite remote failure can be recorded explicitly; unknown remote outcome requires reconciliation. `LeaseStore` keys ownership by resource, token and expiry so an expired owner cannot renew or release a newer owner's lease.

For an existing episode database and asset root, use:

```text
uv run python -m tovitunes.cli --config config.yaml plan <episode-id> --goal render
uv run python -m tovitunes.cli --config config.yaml status <episode-id> --goal render
```

The `release` goal additionally requires commercial-use clearance for every required artifact and its transitive dependencies. This is a dry gate only; there is no upload implementation.

