# Creative drafts and review gates

The pinned `Episode` supplies the curriculum objective, vocabulary, duration, and character-pack versions. A reviewer first approves that objective as an episode decision. `CreativeDraftService` then produces three separate immutable JSON artifact kinds in order:

1. `episode_spec` v2: a concept, premise, cast, setting and structured story beats. At least one beat must teach every target word. There are no lyrics or scene times in this artifact.
2. `lyrics` v1: sectioned lyric lines tied to the exact selected episode-spec artifact ID. Every target word must appear. This is a textual check, not a pronunciation or educational-quality judgment.
3. `music_spec` v1: musical instructions tied to the exact selected lyrics artifact ID, with target duration, sections, tempo suggestion, instrumentation and vocal direction. It prohibits named-artist imitation.

Every draft is ingested through the shared `AssetStore`. Provenance records provider, model, request ID, prompt version, generation time and selected input artifact IDs. New versions and rejected alternatives remain in history. Rights start `unknown`, review starts `pending`, and no draft is selected automatically. `review_candidate(..., actor=...)` records a human decision and selects only an approved eligible version. Rejecting a selected version removes its selection pointer; descendants remain on disk and become stale because their exact dependency is no longer selected.

`FakeDraftGenerator` is an offline contract fixture. Its text has no creative or educational endorsement. It makes it possible to test the workflow without spending money or connecting to a model. A real typed generator can implement `DraftGenerator` later; external requests will need the durable request ledger and lease/reconciliation rules before use.

Example after creating an episode, migrating the DB, and creating an empty `data/generated` directory:

```python
from pathlib import Path

from tovitunes.artifacts.store import AssetStore
from tovitunes.persistence.db import Database
from tovitunes.pipeline.creative import CreativeDraftService, FakeDraftGenerator

db = Database(Path("data/tovitunes.db"))
generated = Path("data/generated")
store = AssetStore(Path("data/assets"), db, generated_source_roots=(generated,))
service = CreativeDraftService(store, generated, FakeDraftGenerator())
service.review_objective(episode_id, "approved", actor="reviewer-name")
concept = service.draft_episode_spec(episode_id)
# Inspect the candidate JSON before an actual reviewer approves it.
service.review_candidate(concept.identity.artifact_id, "approved", actor="reviewer-name")
lyrics = service.draft_lyrics(episode_id)
```

The review methods require an explicit actor but do not authenticate one. A production review UI and authorization layer are future work. The fake generator never invokes a music, image or video provider and never creates a timed storyboard.

