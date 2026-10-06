# Open editorial memory V1

```text
Orchestrator.generate(target)
  -> CreativeService.prepare()
  -> TopicPlanner (existing durable requests and PR #37 fallback)
  -> selected persisted LearningBrief -> generic Episode
  -> existing EpisodeSpec -> LyricsSpec -> MusicSpec
  -> existing ACE-Step -> Audio QA -> EpisodeVisualPlan -> Qwen -> Storyboard V2
  -> Renderer V4 -> Media QA -> MetadataWriter -> release gates -> YouTube
```

PR #38's production engine remains the downstream owner, shared by CLI and WebUI.
New subjects/examples require no Colors YAML additions or familiar-object whitelist.
The historical Colors curriculum remains available for old and manually created episodes.

## Schemas and policy

`LearningPolicy` in creative/learning.py is immutable `open-learning-v1`, with a
content-hashed revision: English, ages 3–6, 30–45 seconds, Tovi as sole mascot/cast,
one concrete visually demonstrable objective, at most four vocabulary terms, safe
familiar activities, simple language, musical repetition and production feasibility.
It prohibits adult/political content, dangerous imitation, copyrighted character and
named-artist imitation, and purchasing manipulation. Example domains are explicitly
non-exhaustive guidance. Adding a lesson does not require changing this policy.

`TopicCandidate` fields: subject, domain, objective, target_vocabulary, premise, hook,
setting, example_objects, song_angle, working_title, score and reason. Extra fields,
including model-chosen IDs, are forbidden. Vocabulary/examples contain 1–4 distinct
terms, each 1–3 lowercase English words. Text fields have length bounds; score is
finite 0–10. `TopicPool` contains 1–30 candidates and must match the configured exact
batch size. Stable descending score sorting precedes deterministic candidate checks.
Scores are batch-relative ordering signals, never views/CTR/retention/virality predictions.

`LearningBrief` adds schema_version=1, application-derived brief_id, normalized_subject,
idea_fingerprint, learning_policy_revision_id, language=en, duration (30–45) and created_at.
Database triggers protect all selected facts. The generated Episode pins those identities,
subject/objective/vocabulary, language/duration, brand revision and character-pack revisions.
It explicitly uses `learning_source=generated_learning_brief`, with no curriculum revision.
Its key combines a bounded subject slug, educational hash and collision-checked ordinal.
UUIDs and artifact IDs remain application-owned. Generated subjects are not Colors entries.

The selected brief is input to creative drafting. EpisodeSpec IDs/vocabulary, LyricsSpec
IDs/vocabulary, MusicSpec IDs/vocabulary/duration and exact pins are validated. The existing
canonical music adapter copies the exact objective; visual planning receives pinned episode
facts and its existing validators prohibit unsupported claims. MetadataWriter receives the
brief plus actual final render/storyboard/lyrics facts and writes a separate final metadata
artifact. The early working title remains provisional; no hard-coded title template is needed.

Policy checks cover prohibited content, bounded vocabulary/text, one objective, explicit
vocabulary coverage, visible teaching actions and concrete examples. These are conservative
lexical/structural admission checks, not a complete semantic safety/artistic certification.
Existing human review overrides, media QA, commercial rights and release gates still apply.

## Memory, duplicates and concurrency

All memory uses ToviTunes' existing Database. `learning_briefs` holds selected JSON, brand,
normalized subject, canonical fingerprint, policy revision, run/ordinal, creation time,
reserved episode UUID and actual provider/model/local generation-request provenance.
It references existing creative_runs and generation_requests. Selection commits immediately
before even EpisodeSpec, and therefore before music/image/render work. Failed, unpublished,
held and archived ideas remain excluded. A crash after selection reuses the persisted brief;
a crash after episode reservation reuses the same UUID/key.

TopicMemory projects all generated selections and legacy episodes from their persisted
objectives/vocabulary, retained subject treatments where available, selected immutable production artifacts
and publication_attempts. Prompt history is compact subject/domain/objective/vocabulary,
premise/hook/examples/working-title/status data. No binary data, artifact records, provenance
or embeddings enter the topic prompt. Recent-history count bounds only the prompt;
deterministic comparison uses the complete durable history, including legacy Colors lessons.

Normalization adapts donor NFKC Unicode normalization, case folding, punctuation-to-spaces
and collapsed whitespace. Lexical similarity takes the maximum of sequence ratio, token
Jaccard and bounded containment. Educational comparisons cover subject + objective +
vocabulary, sorted vocabulary and subject signatures with grammatical framing words removed.
Canonical identity hashes an objective signature and sorted vocabulary. Identical small
vocabulary sets are conservatively treated as repeated lessons, even with a different title
or props. Creative-treatment comparison separately covers premise + hook + examples using
the existing find/discover signature. Generic framing alone does not exclude distinct ideas.
Thresholds for educational, treatment and semantic checks are configured explicitly.

UNIQUE `(brand_id,normalized_subject)` and `(brand_id,idea_fingerprint)` constraints independently
fence exact races. BEGIN IMMEDIATE rechecks current history under the write lock, also fencing
concurrent lexical duplicates. JSON identity checks and immutable triggers protect briefs and
policy revisions. Generated episode learning fact columns cannot be updated.

`TopicPlanner.select(..., count=N)` commits each selection, adds it to comparison memory and
injects same_run_exclusions into later round prompts. Partial selections remain durable even
when subsequent pools exhaust. The canonical workflow uses count=1; multi-selection uses this
same planner. No competing preview/planning implementation exists.

## Durable rounds and optional embeddings

`topic_rounds` freezes each pool's exact inputs, ordinal, completion and rejection reasons.
Requests use the existing generator/ledger and Kimi → GLM → Nemotron → DeepSeek chain with
actual model provenance, saved receipts, visible schema repair and ambiguity rules. Invalid
completed pools advance only up to max_generation_rounds, including across process restarts.
Ambiguous requests block immediately. Topic config is pinned; restore it for unfinished runs.
There is no new NVIDIA client, transport retry or hidden unbounded regeneration.

Embeddings default to disabled. A small independent EmbeddingProvider interface supports
explicit local Ollama `/api/embed` configuration only; it never downloads/pulls a model.
NVIDIA generation does not depend on embeddings. Finite nonzero cosine vectors are optional
search data in editorial_embeddings keyed by provider/endpoint/model identity. Enabling a model
backfills missing generated and legacy history; changing identity creates separate search data.
Failures/malformed vectors degrade to exact + lexical checking without saving provider error
text. Ownership fencing remains mandatory, and vectors never modify educational facts.

## Operator commands and configuration

```powershell
uv run --locked python -m tovitunes.cli --config config.yaml creative generate-next --live
uv run --locked python -m tovitunes.cli --config config.yaml creative generate-next --episode-key colors-blue-001
uv run --locked python -m tovitunes.cli --config config.yaml creative doctor
uv run --locked python -m tovitunes.cli --config config.yaml creative history
```

Incomplete runs resume before creating a fresh one. `creative eligible` remains a legacy
curriculum inspection command and does not govern new autonomous creation. Doctor/history
instantiate no generation or embedding client, including when embeddings are enabled.
Doctor reports open mode, settings, selected-history count and primary/fallback chain.
Credentials remain outside configuration/persistence/output; parsing never probes providers.

Defaults: batch=15 (1–30), prompt history=100 (1–500), rounds=3 (1–10), educational lexical=.82,
treatment=.80, semantic=.88 (each >0, <=1), bounded optional banned_topics. Embeddings require
enabled=true plus an explicit model, local credential-free endpoint and bounded timeout.
See creative_topics in config.example.yaml. No remote calls occur during config parsing.

## Migration and historical compatibility

0020 adds policy, brief, optional-vector and round tables and rebuilds only episodes so generated
sources can have a null curriculum FK. Database.migrate disables FK enforcement before that
transaction, copies every original column verbatim without renaming the old table (preserving
inbound FK SQL), replaces episodes, checks foreign_key_check before commit and restores FK
enforcement. Errors roll back; old migration checksums remain unchanged; reruns are idempotent.
Legacy rows receive only source defaults, no replacement facts, revisions or selections.

Legacy Episode JSON omits the new default fields to preserve PR #37 prompt/receipt fingerprints.
Legacy prompts and incomplete subject-planner-v1 runs retain their previous schema/selection
path. colors-blue-001 resumes selected EpisodeSpec/LyricsSpec/MusicSpec without regeneration.
Published Colors–Red retains the engine's historical path. The pre-editorial regression test
snapshots every original table column, published Red records, selected Blue creative artifacts
and all artifact bytes, checks them after migration twice and resumes with no provider calls.
Tests use offline fixtures; the operator's production database/assets are not accessed.

## Pinned donor study

Studied [ollama-mpt-youtube at 7ef50aaf9b4aa13590a6edba5034e2fbad56cc79](https://github.com/egegoktugbozkir35/ollama-mpt-youtube/tree/7ef50aaf9b4aa13590a6edba5034e2fbad56cc79):
app/llm/topic_generator.py, prompts.py, production_brief.py, app/memory/store.py, similarity.py,
app/state/db.py, migrations.py, app/models.py, orchestrator.py and tests/test_topic_planner.py.

Adapted TopicPlanner pool/ranking, recent-history injection, same-run exclusions, topic
normalization, lexical similarity, optional cosine embeddings and history backfill, persistent
exact uniqueness, immediate selection memory and bounded generation rounds. ToviTunes extends
comparison to educational facts and creative treatments, freezes round inputs durably and
separates preschool working-title selection from post-render publication metadata.

Intentionally not copied: ContentStore/content_items database, donor execution lease,
publication states/uploaders, generic factual-channel/causal prompt wording, MoneyPrinterTurbo
rendering, production-brief beat system or parallel audit ledger. ToviTunes already has stronger
specialized Database/AssetStore, generation_requests and PR #37 fallback, execution fencing and
PR #38 recovery, creative/visual contracts, ACE-Step/Qwen/Renderer V4, reviews, rights and YouTube.

Offline tests demonstrate an invented texture lesson beyond policy examples, history-driven
repeat rejection, concurrency fences, same-run/multi-round behavior, restart durability, optional
embedding backfill/failure, actual fallback provenance and a generated lesson reaching a real
MP4 and separate final metadata through the original production engine. All providers are mocked.
