CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE brand_revisions (
    revision_id TEXT PRIMARY KEY,
    brand_id TEXT NOT NULL,
    version TEXT NOT NULL,
    definition_sha256 TEXT NOT NULL,
    creative_sha256 TEXT NOT NULL,
    safety_sha256 TEXT NOT NULL,
    source_revision TEXT,
    UNIQUE (brand_id, version, definition_sha256, creative_sha256, safety_sha256)
);

CREATE TABLE curriculum_revisions (
    revision_id TEXT PRIMARY KEY,
    curriculum_id TEXT NOT NULL,
    version TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    UNIQUE (curriculum_id, version, sha256)
);

CREATE TABLE character_pack_revisions (
    revision_id TEXT PRIMARY KEY,
    pack_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    readiness TEXT NOT NULL CHECK (readiness IN ('draft', 'approved')),
    UNIQUE (character_id, version, manifest_sha256)
);

CREATE TABLE episodes (
    episode_id TEXT PRIMARY KEY,
    external_key TEXT NOT NULL UNIQUE,
    brand_revision_id TEXT NOT NULL REFERENCES brand_revisions(revision_id),
    curriculum_revision_id TEXT NOT NULL REFERENCES curriculum_revisions(revision_id),
    concept_id TEXT NOT NULL,
    objective_id TEXT NOT NULL,
    objective TEXT NOT NULL,
    target_vocabulary_json TEXT NOT NULL,
    language TEXT NOT NULL,
    target_duration_seconds INTEGER NOT NULL CHECK (target_duration_seconds > 0),
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('draft', 'active', 'held', 'complete', 'archived')),
    created_at TEXT NOT NULL
);

CREATE INDEX episodes_concept_created ON episodes(concept_id, created_at);

CREATE TABLE episode_character_packs (
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE RESTRICT,
    character_id TEXT NOT NULL,
    revision_id TEXT NOT NULL REFERENCES character_pack_revisions(revision_id),
    PRIMARY KEY (episode_id, character_id)
);

CREATE TABLE artifact_versions (
    artifact_id TEXT PRIMARY KEY,
    owner_scope TEXT NOT NULL CHECK (owner_scope IN ('episode', 'brand')),
    episode_id TEXT REFERENCES episodes(episode_id),
    brand_revision_id TEXT REFERENCES brand_revisions(revision_id),
    kind TEXT NOT NULL,
    slot_key TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version > 0),
    relative_path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,
    byte_count INTEGER NOT NULL CHECK (byte_count > 0),
    mime_type TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK ((owner_scope = 'episode' AND episode_id IS NOT NULL AND brand_revision_id IS NULL)
        OR (owner_scope = 'brand' AND brand_revision_id IS NOT NULL AND episode_id IS NULL))
);

CREATE INDEX artifact_versions_episode_slot ON artifact_versions(episode_id, kind, slot_key, created_at);
CREATE INDEX artifact_versions_hash ON artifact_versions(sha256);

CREATE TABLE artifact_selections (
    owner_scope TEXT NOT NULL CHECK (owner_scope IN ('episode', 'brand')),
    owner_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    slot_key TEXT NOT NULL,
    artifact_id TEXT NOT NULL REFERENCES artifact_versions(artifact_id),
    selected_at TEXT NOT NULL,
    PRIMARY KEY (owner_scope, owner_id, kind, slot_key)
);

CREATE TABLE artifact_dependencies (
    consumer_artifact_id TEXT NOT NULL REFERENCES artifact_versions(artifact_id),
    input_artifact_id TEXT NOT NULL REFERENCES artifact_versions(artifact_id),
    input_sha256 TEXT NOT NULL,
    purpose TEXT NOT NULL,
    PRIMARY KEY (consumer_artifact_id, input_artifact_id, purpose),
    CHECK (consumer_artifact_id <> input_artifact_id)
);

CREATE INDEX artifact_dependencies_input ON artifact_dependencies(input_artifact_id);

CREATE TABLE rights_decisions (
    decision_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES artifact_versions(artifact_id),
    status TEXT NOT NULL CHECK (status IN ('unknown', 'review_required', 'commercial_use_confirmed', 'blocked')),
    actor TEXT NOT NULL,
    evidence_uri TEXT,
    policy_version TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    CHECK (status <> 'commercial_use_confirmed' OR evidence_uri IS NOT NULL)
);

CREATE INDEX rights_decisions_artifact_time ON rights_decisions(artifact_id, decided_at);

CREATE TABLE approval_decisions (
    decision_id TEXT PRIMARY KEY,
    episode_id TEXT REFERENCES episodes(episode_id),
    artifact_id TEXT REFERENCES artifact_versions(artifact_id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'needs_review')),
    actor TEXT NOT NULL,
    reason TEXT,
    policy_version TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    CHECK ((episode_id IS NOT NULL) <> (artifact_id IS NOT NULL)),
    CHECK (status <> 'rejected' OR reason IS NOT NULL)
);

CREATE INDEX approval_decisions_episode_time ON approval_decisions(episode_id, decided_at);
CREATE INDEX approval_decisions_artifact_time ON approval_decisions(artifact_id, decided_at);

