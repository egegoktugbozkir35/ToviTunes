# Public release V1

The local WebUI uses `MetadataWriter.generate()` through `CreativeWorkflow` for
`POST /api/episodes/{episode_key}/publication-metadata`. The writer reuses valid
selected metadata with the same pinned inputs, preserves provider request
provenance for new output, runs structural validation, records the existing
machine metadata approval, and selects the artifact. The frozen V4 pilot uses
its selected timed storyboard as the creative source because it predates
`EpisodeSpec` and `LyricsSpec`; no creative or media artifact is regenerated.

For the frozen `colors-red-001` release, the operator-approved replacement
[publication metadata](rights/COLORS_RED_OPERATOR_METADATA.md) is now selected.
It carries manual provenance, the same render and dependency SHA pins, and an
explicit artifact approval. The historical NVIDIA/Kimi version is retained but
unselected. No provider or renderer was called for this replacement.

`POST /api/episodes/{episode_key}/youtube/upload-private` remains the only
upload action. It inserts a new video with `privacyStatus=private`,
`selfDeclaredMadeForKids=true`, and the configured synthetic-media setting.
The durable attempt prevents another upload for the same episode, render SHA,
and metadata fingerprint.

`POST /api/episodes/{episode_key}/youtube/publish` accepts no video ID. It
uses the ID from the successful private attempt and rejects missing or changed
release evidence. It asserts the expected OAuth channel, reads the remote
video, requires exact ID, private privacy, processed upload, successful
processing, and matching child/synthetic-media status, then reruns public
preflight. The transition uses `videos.update(part="status")` on that same ID.
The upload attempt remains intact; migration `0017` stores a separate durable
visibility event with the input IDs, hashes, timestamps, outcome, and safe
failure summary. After remote start, an uncertain update is recorded as
`ambiguous` and blocks automatic retry. The WebUI requires an explicit
confirmation and enables the button only after a fresh remote status check.

The OAuth token now needs `youtube.force-ssl` in addition to upload and read
scopes for `videos.update`; existing tokens need an explicit reconnect. The
current [YouTube videos.update reference](https://developers.google.com/youtube/v3/docs/videos/update)
also warns that omitted mutable fields in the updated `status` part can be
deleted. The client retains the reported embeddable, license, and public stats
settings while changing privacy and explicitly retaining made-for-kids and
synthetic-media declarations.

Rights evaluation keeps the complete immutable dependency graph while applying
independent commercial evidence only to non-deterministic creative source
roots. Derived artifacts receive `commercial_rights_inherited` checks over
their validated ancestor closure. Unknown or review-required decisions on a
derived artifact do not demand duplicate evidence, while an explicit blocked
ancestor and every technical or approval failure remain blocking.

Rights decisions for direct roots in the selected dependency graph are appended through
`python -m tovitunes.publication.rights --config config.yaml --evidence reviewed.json`.
Use `--snapshot colors-red-001` to export a read-only exact graph template for
review after metadata is selected. The snapshot freezes all graph SHAs and the
direct/derived classification, but requests decisions only for uncleared direct
roots. The [rights evidence record](rights/COLORS_RED_RELEASE_RIGHTS.md)
documents the current cleared graph. All 20 direct roots are commercially
cleared, the unresolved count is zero, and provider-free public release
preflight passes. Private upload, processing verification, and same-video
`videos.update(part="status")` public promotion remain the operator flow.
