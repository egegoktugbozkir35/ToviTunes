# Creative domain

`CreativeService` supplies the preschool-specific stages inside `Orchestrator.generate` / `resume`: TopicPlanner, a selected immutable LearningBrief, EpisodeSpec, LyricsSpec and MusicSpec. Pins, safety policy, cast, vocabulary and duration validation remain ToviTunes contracts. Prompts live in `creative/prompts.py`.

NVIDIA NIM uses the configured ordered Kimi, GLM, Nemotron, DeepSeek chain. The factory applies the donor bounded request timeout (at most 120 seconds). Each model may make one initial request and one schema/domain repair. Successful fallback is sticky across stages and process restarts. The single classifier in `creative/failures.py` assigns MODEL, ENDPOINT or FAIL_CLOSED; optional Ollama handles eligible endpoint failure only.

An ambiguous creative request remains immutable, is never resent, and automatically advances to the next configured model. No manual reconciliation participates in new production. Authentication, configuration, rate-limit and unexpected programming failures fail closed. Old PR40 reconciliation rows can be inspected with `creative request-status` and remain immutable historical evidence.

`creative generate-next --live` delegates to the application DRAFT target. Existing episode generation delegates to `resume(..., DRAFT)`. `creative doctor`, `creative history`, `creative eligible` and `creative request-status --request-id ...` are read-only inspection commands. Metadata is the RENDER target's final domain stage.

See [editorial memory](OPEN_EDITORIAL_MEMORY_V1.md) for topic policy and [the migration report](MPT_ARCHITECTURE_MIGRATION.md) for runtime ownership and validation. Historical curriculum branches only read or migrate already persisted Colors identity; new autonomous production always uses open topic planning.
