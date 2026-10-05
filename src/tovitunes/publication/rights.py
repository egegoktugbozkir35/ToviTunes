"""Explicit append-only commercial rights closeout for a reviewed release graph."""

from __future__ import annotations

import argparse
import json
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from tovitunes.artifacts.store import AssetStore
from tovitunes.config import RuntimeConfig, load_config
from tovitunes.domain.review import RightsDecision
from tovitunes.persistence.db import Database
from tovitunes.publication.preflight import evaluate_release
from tovitunes.publication.rights_policy import RIGHTS_APPLICABILITY_POLICY

POLICY = "tovitunes_publication_rights_v1"


def reviewed_graph_template(config: RuntimeConfig, episode_key: str) -> dict[str, Any]:
    """Produce an exact, read-only evidence template for human review."""
    report = evaluate_release(config, episode_key)
    if not report.render_artifact_id or not report.metadata_artifact_id:
        raise ValueError("Selected final render and publication metadata are required")
    if any(
        not check.passed and check.scope not in {"rights_private", "rights_public"}
        for check in report.checks
    ):
        raise ValueError("Selected release graph has invalid technical or approval evidence")
    store = AssetStore(config.data_root, Database(config.database_path), initialize=False)
    ids = sorted(
        {
            check.artifact_id
            for check in report.checks
            if check.name == "immutable_sha" and check.artifact_id
        }
    )
    graph = {aid: store.get(aid).sha256 for aid in ids}
    uncleared = {
        check.artifact_id
        for check in report.checks
        if check.name == "commercial_rights_direct" and not check.passed
    }
    direct_ids = sorted(
        {
            check.artifact_id
            for check in report.checks
            if check.name == "commercial_rights_direct" and check.artifact_id
        }
    )
    derived_ids = sorted(
        {
            check.artifact_id
            for check in report.checks
            if check.name == "commercial_rights_inherited" and check.artifact_id
        }
    )
    return {
        "episode_key": episode_key,
        "render_artifact_id": report.render_artifact_id,
        "render_sha256": report.render_sha256,
        "metadata_artifact_id": report.metadata_artifact_id,
        "metadata_fingerprint": report.metadata_fingerprint,
        "graph_sha256": graph,
        "rights_policy_version": RIGHTS_APPLICABILITY_POLICY,
        "direct_rights_roots": {
            aid: {
                "sha256": graph[aid],
                "kind": store.get(aid).identity.kind,
                "slot_key": store.get(aid).identity.slot_key,
                "source_kind": store.get(aid).provenance.source_kind,
            }
            for aid in direct_ids
        },
        "derived_artifact_ids": derived_ids,
        "decisions": {
            aid: {
                "sha256": graph[aid],
                "kind": store.get(aid).identity.kind,
                "slot_key": store.get(aid).identity.slot_key,
                "actor": "",
                "evidence_uri": "",
                "rationale": "",
                "decided_at": "",
            }
            for aid in ids
            if aid in uncleared
        },
    }


def closeout_rights(config: RuntimeConfig, evidence: dict[str, Any]) -> list[dict[str, str]]:
    """Reject any selection/hash/graph drift before appending a single decision."""
    key = evidence.get("episode_key")
    if not isinstance(key, str):
        raise ValueError("Evidence must name an episode")
    report = evaluate_release(config, key)
    if not report.render_artifact_id or not report.metadata_artifact_id:
        raise ValueError("Selected final render and publication metadata are required")
    if (
        evidence.get("render_artifact_id") != report.render_artifact_id
        or evidence.get("render_sha256") != report.render_sha256
        or evidence.get("metadata_artifact_id") != report.metadata_artifact_id
        or evidence.get("metadata_fingerprint") != report.metadata_fingerprint
    ):
        raise ValueError("Reviewed release inputs differ from selected inputs")
    non_rights = [
        check
        for check in report.checks
        if check.scope not in {"rights_private", "rights_public"} and not check.passed
    ]
    if non_rights:
        raise ValueError("Selected release graph has invalid technical or approval evidence")
    database = Database(config.database_path)
    store = AssetStore(config.data_root, database, initialize=False)
    ids = {check.artifact_id for check in report.checks if check.name == "immutable_sha"}
    graph = {aid: store.get(aid).sha256 for aid in ids if aid is not None}
    if evidence.get("graph_sha256") != graph:
        raise ValueError("Reviewed dependency graph differs from selected release graph")
    current_template = reviewed_graph_template(config, key)
    if (
        evidence.get("rights_policy_version") != RIGHTS_APPLICABILITY_POLICY
        or evidence.get("direct_rights_roots") != current_template["direct_rights_roots"]
        or evidence.get("derived_artifact_ids") != current_template["derived_artifact_ids"]
    ):
        raise ValueError("Reviewed rights applicability differs from selected release graph")
    decisions = evidence.get("decisions")
    direct_roots = {
        check.artifact_id
        for check in report.checks
        if check.name == "commercial_rights_direct" and check.artifact_id
    }
    uncleared = {
        check.artifact_id
        for check in report.checks
        if check.name == "commercial_rights_direct" and not check.passed
    }
    if (
        not isinstance(decisions, dict)
        or not uncleared.issubset(decisions)
        or not set(decisions).issubset(direct_roots)
    ):
        raise ValueError("Rights evidence must cover only uncleared direct rights roots")
    prepared: list[RightsDecision] = []
    for aid, detail in decisions.items():
        if not isinstance(detail, dict) or not isinstance(aid, str):
            raise ValueError("Invalid rights evidence entry")
        record = store.get(aid)
        if not store.inspect(aid).valid or record.sha256 != graph[aid]:
            raise ValueError("Reviewed artifact hash is no longer valid")
        if (
            detail.get("sha256") != graph[aid]
            or detail.get("kind") != record.identity.kind
            or detail.get("slot_key") != record.identity.slot_key
            or not isinstance(detail.get("actor"), str)
            or not detail["actor"].strip()
            or not isinstance(detail.get("evidence_uri"), str)
            or not detail["evidence_uri"].strip()
            or not isinstance(detail.get("rationale"), str)
            or not detail["rationale"].strip()
        ):
            raise ValueError("Rights evidence does not match artifact identity or is incomplete")
        prepared.append(
            RightsDecision(
                artifact_id=aid,
                status="commercial_use_confirmed",
                actor=detail["actor"],
                evidence_uri=detail["evidence_uri"],
                rationale=detail["rationale"],
                policy_version=POLICY,
                decided_at=datetime.fromisoformat(detail["decided_at"]),
            )
        )
    changed: list[dict[str, str]] = []
    with closing(database.connect()) as connection:
        current = {
            aid: connection.execute(
                "SELECT status FROM rights_decisions WHERE artifact_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (aid,),
            ).fetchone()
            for aid in graph
        }
    for decision in prepared:
        previous = current[decision.artifact_id]
        old = str(previous[0]) if previous else "missing"
        if old == "commercial_use_confirmed":
            continue
        # The store is the sole writer. Historical rows remain untouched.
        store.record_rights(decision)
        changed.append(
            {"artifact_id": decision.artifact_id, "old": old, "new": "commercial_use_confirmed"}
        )
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description="Append reviewed publication rights decisions")
    parser.add_argument("--config", type=Path, required=True)
    task = parser.add_mutually_exclusive_group(required=True)
    task.add_argument("--evidence", type=Path)
    task.add_argument("--snapshot", metavar="EPISODE_KEY")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.snapshot:
        print(json.dumps(reviewed_graph_template(config, args.snapshot), indent=2))
    else:
        assert args.evidence is not None
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        print(json.dumps(closeout_rights(config, evidence), sort_keys=True))


if __name__ == "__main__":
    main()
