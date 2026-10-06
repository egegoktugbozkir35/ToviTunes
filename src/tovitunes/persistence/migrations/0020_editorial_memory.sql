-- Database.migrate disables FK enforcement before this transaction and checks all
-- inbound references before committing. Never rename the old episodes table:
-- child table SQL continues to reference the final episodes name.
CREATE TABLE learning_policy_revisions (
    revision_id TEXT PRIMARY KEY,
    policy_json TEXT NOT NULL CHECK (json_valid(policy_json))
);
CREATE TABLE learning_briefs (
    brief_id TEXT PRIMARY KEY,
    brand_id TEXT NOT NULL,
    normalized_subject TEXT NOT NULL CHECK (length(normalized_subject)>0),
    idea_fingerprint TEXT NOT NULL,
    brief_json TEXT NOT NULL CHECK (json_valid(brief_json)),
    learning_policy_revision_id TEXT NOT NULL REFERENCES learning_policy_revisions(revision_id),
    run_id TEXT NOT NULL REFERENCES creative_runs(run_id),
    ordinal INTEGER NOT NULL CHECK (ordinal>0),
    reserved_episode_id TEXT UNIQUE,
    generation_request_id TEXT REFERENCES generation_requests(request_id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(brand_id,normalized_subject), UNIQUE(brand_id,idea_fingerprint),
    UNIQUE(run_id,ordinal),
    CHECK(json_extract(brief_json,'$.brief_id')=brief_id),
    CHECK(json_extract(brief_json,'$.idea_fingerprint')=idea_fingerprint),
    CHECK(json_extract(brief_json,'$.normalized_subject')=normalized_subject),
    CHECK(json_extract(brief_json,'$.learning_policy_revision_id')=learning_policy_revision_id)
);
CREATE TABLE editorial_embeddings (
    memory_id TEXT NOT NULL,
    embedding_identity TEXT NOT NULL,
    vector_json TEXT NOT NULL CHECK (json_valid(vector_json)),
    PRIMARY KEY(memory_id,embedding_identity)
);
CREATE TABLE topic_rounds (
    run_id TEXT NOT NULL REFERENCES creative_runs(run_id),
    ordinal INTEGER NOT NULL CHECK (ordinal>0),
    input_json TEXT NOT NULL CHECK (json_valid(input_json)),
    completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
    rejections_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(rejections_json)),
    PRIMARY KEY(run_id,ordinal)
);
CREATE TABLE episodes_new (
    episode_id TEXT PRIMARY KEY,
    external_key TEXT NOT NULL UNIQUE,
    brand_revision_id TEXT NOT NULL REFERENCES brand_revisions(revision_id),
    curriculum_revision_id TEXT REFERENCES curriculum_revisions(revision_id),
    concept_id TEXT NOT NULL,
    objective_id TEXT NOT NULL,
    objective TEXT NOT NULL,
    target_vocabulary_json TEXT NOT NULL,
    language TEXT NOT NULL,
    target_duration_seconds INTEGER NOT NULL CHECK(target_duration_seconds>0),
    lifecycle TEXT NOT NULL CHECK(lifecycle IN ('draft','active','held','complete','archived')),
    created_at TEXT NOT NULL,
    learning_source TEXT NOT NULL DEFAULT 'legacy_curriculum'
        CHECK(learning_source IN ('legacy_curriculum','generated_learning_brief')),
    learning_brief_id TEXT UNIQUE REFERENCES learning_briefs(brief_id),
    learning_policy_revision_id TEXT REFERENCES learning_policy_revisions(revision_id),
    subject TEXT,
    CHECK((learning_source='legacy_curriculum' AND curriculum_revision_id IS NOT NULL
        AND learning_brief_id IS NULL AND learning_policy_revision_id IS NULL AND subject IS NULL)
        OR (learning_source='generated_learning_brief' AND curriculum_revision_id IS NULL
        AND learning_brief_id IS NOT NULL AND learning_policy_revision_id IS NOT NULL
        AND subject IS NOT NULL))
);
INSERT INTO episodes_new(episode_id,external_key,brand_revision_id,curriculum_revision_id,
    concept_id,objective_id,objective,target_vocabulary_json,language,
    target_duration_seconds,lifecycle,created_at)
SELECT episode_id,external_key,brand_revision_id,curriculum_revision_id,concept_id,
    objective_id,objective,target_vocabulary_json,language,target_duration_seconds,lifecycle,
    created_at FROM episodes;
DROP TABLE episodes;
ALTER TABLE episodes_new RENAME TO episodes;
CREATE INDEX episodes_concept_created ON episodes(concept_id,created_at);
CREATE TRIGGER learning_brief_immutable BEFORE UPDATE OF brief_id,brand_id,normalized_subject,
    idea_fingerprint,brief_json,learning_policy_revision_id,run_id,ordinal,
    generation_request_id,provider,model,created_at ON learning_briefs
BEGIN SELECT RAISE(ABORT,'selected learning facts are immutable'); END;
CREATE TRIGGER learning_brief_no_delete BEFORE DELETE ON learning_briefs
BEGIN SELECT RAISE(ABORT,'selected idea memory cannot be deleted'); END;
CREATE TRIGGER learning_policy_immutable BEFORE UPDATE ON learning_policy_revisions
BEGIN SELECT RAISE(ABORT,'learning policy revision is immutable'); END;
CREATE TRIGGER learning_policy_no_delete BEFORE DELETE ON learning_policy_revisions
BEGIN SELECT RAISE(ABORT,'learning policy revision cannot be deleted'); END;
CREATE TRIGGER generated_episode_facts BEFORE UPDATE OF concept_id,objective_id,objective,
    target_vocabulary_json,language,target_duration_seconds,learning_source,
    learning_brief_id,learning_policy_revision_id,subject ON episodes
WHEN OLD.learning_source='generated_learning_brief'
BEGIN SELECT RAISE(ABORT,'selected episode learning facts are immutable'); END;
