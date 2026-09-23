# Artifact store

`AssetStore` ingests provider outputs and operator files through the same path. Call `Database.migrate()` first, then create an episode (or use an existing brand revision) before ingesting its files.

Ingestion copies bytes into a same-volume staging area, checks the file signature or UTF-8 payload, computes SHA-256 and size, then atomically places the file under `data/episodes/<episode-id>/<kind>/<slot>/<artifact-id>.<ext>` or `data/brand-assets/...`. Registration of the immutable version, pinned dependencies and initial validation occurs in one SQLite transaction. If the transaction fails after final placement, the file is an orphan; it is never assumed to be accepted.

`Provenance` records manual operator/source details or provider/model/request details. Rights and approval decisions are separate append-only rows. No decision is inferred from successful file registration. An artifact can be selected only after current approval, file integrity and selected dependency checks pass. Unknown rights may permit intermediate selection, but a blocked rights decision prevents use. Publication will require explicit commercial clearance across all inputs in a later phase.

Selecting a new artifact in the same logical slot supersedes the old selection while leaving old bytes and decisions intact. A dependent artifact becomes unusable when its pinned input is no longer selected or its bytes change. `inspect()` rechecks the path, signature, size and hash instead of trusting a past validation result.

Run `quarantine_orphans()` only at startup or while ingestion is paused. It moves unregistered files from staging, episode and brand-asset areas into `data/quarantine/`; it does not delete them or turn them into artifact records. Operators can inspect those files and ingest a known valid one explicitly.

The current media check is deliberately limited to supported extensions and basic signatures or UTF-8 parsing. It does not prove an image fully decodes or an MP4 has valid streams, duration, loudness or educational content. Those checks belong to later media QA.

