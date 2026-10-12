-- Explicit append-only authorization for a new visual generation contract.
-- No existing evidence, migration, request identity or episode is rewritten.
CREATE TABLE visual_contract_recoveries (
    recovery_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    old_prompt_version TEXT NOT NULL CHECK(old_prompt_version='episode_visual_plan_v1'),
    new_prompt_version TEXT NOT NULL CHECK(new_prompt_version='visual_creative_v2'),
    evidence_fingerprint TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(episode_id,new_prompt_version)
);
CREATE TRIGGER visual_recovery_immutable_update BEFORE UPDATE ON visual_contract_recoveries
BEGIN SELECT RAISE(ABORT,'visual recovery authorizations are immutable'); END;
CREATE TRIGGER visual_recovery_immutable_delete BEFORE DELETE ON visual_contract_recoveries
BEGIN SELECT RAISE(ABORT,'visual recovery authorizations are immutable'); END;
