"""ToviTunes music algorithms; application lifecycle belongs to Orchestrator."""

import json
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from tovitunes.domain.artifact import Provenance
from tovitunes.domain.creative import EpisodeSpec, LyricsSpec, MusicSpec
from tovitunes.domain.episode import Episode
from tovitunes.errors import ProductionStop
from tovitunes.music.ace_step import AceStepLocalProvider
from tovitunes.music.analysis import AnalysisConfig
from tovitunes.music.benchmark import MusicBenchmark
from tovitunes.music.benchmark import plan as plan_music
from tovitunes.pipeline.music_adapter import creative_music_spec
from tovitunes.render.episode_assets import persist_file
from tovitunes.services.context import StageContext


class MusicService(StageContext):
    def _music(
        self,
        episode: Episode,
        inputs: tuple[EpisodeSpec, LyricsSpec, MusicSpec, tuple[str, str, str]],
    ) -> tuple[str, str]:
        spec, lyrics, music, ids = inputs
        canonical = creative_music_spec(episode, spec, lyrics, music, ids)
        adapter = self._json(
            episode,
            "creative_music_input",
            {
                "adapter_version": "creative_music_v1",
                "episode_id": episode.episode_id,
                "creative_artifact_ids": ids,
                "canonical_spec": canonical.model_dump(mode="json"),
            },
            ids,
        )
        benchmark = MusicBenchmark(self.database, self.config.data_root / "music-benchmark")
        with closing(self.database.connect()) as db:
            binding = db.execute(
                "SELECT * FROM episode_music_bindings WHERE episode_id=?", (episode.episode_id,)
            ).fetchone()
        if binding:
            if binding["adapter_artifact_id"] != adapter.identity.artifact_id:
                raise ProductionStop(
                    "BLOCKED", "Selected creative inputs differ from the immutable music binding"
                )
            request = benchmark.request(binding["request_id"])
            if request["status"] != "succeeded" and (
                binding["provider_endpoint"] != self.config.music_generation.base_url
            ):
                raise ProductionStop(
                    "BLOCKED", "Music endpoint differs from the durable task binding"
                )
            if request["status"] == "succeeded":
                result = benchmark.reconcile(request["request_id"])
            else:
                with closing(self.database.connect()) as db:
                    receipt = db.execute(
                        "SELECT 1 FROM music_receipts WHERE request_id=?", (request["request_id"],)
                    ).fetchone()
                if receipt:
                    result = benchmark.reconcile(request["request_id"])
                elif request["status"] == "ambiguous":
                    if request["provider_request_id"]:
                        provider = self.music_provider or AceStepLocalProvider(
                            self.config.music_generation
                        )
                        result = benchmark.provider_resume(request["request_id"], provider)
                    else:
                        raise ProductionStop(
                            "AMBIGUOUS",
                            "Music interaction is uncertain; inspect its retained task",
                            {
                                "request_id": request["request_id"],
                                "recovery_action": "resume_music_task"
                                if request["provider_request_id"]
                                else None,
                            },
                        )
                elif request["status"] in {"retryable_failure", "terminal_failure"}:
                    raise ProductionStop(
                        "FAILED",
                        "Music request failed; explicit operator recovery is required",
                        {"request_id": request["request_id"]},
                    )
                else:
                    provider = self.music_provider or AceStepLocalProvider(
                        self.config.music_generation
                    )
                    if request["remote_started_at"]:
                        if not request["provider_request_id"]:
                            raise ProductionStop(
                                "AMBIGUOUS",
                                "Music remote start lacks task identity",
                                {"request_id": request["request_id"]},
                            )
                        result = benchmark.provider_resume(request["request_id"], provider)
                    else:
                        item = plan_music(canonical.brief, canonical.lyrics, [provider], attempt=1)[
                            0
                        ]
                        if item.input_fingerprint != request["input_fingerprint"]:
                            raise ProductionStop(
                                "BLOCKED",
                                "Music provider configuration differs from prepared request",
                            )
                        result = benchmark.run(item, provider)
        else:
            if self.config.music_generation.provider != "ace_step_local":
                raise ProductionStop(
                    "BLOCKED", "New production music requires configured ace_step_local"
                )
            provider = self.music_provider or AceStepLocalProvider(self.config.music_generation)
            item = plan_music(canonical.brief, canonical.lyrics, [provider], attempt=1)[0]
            request = benchmark.prepare(item)
            with closing(self.database.connect()) as db:
                db.execute(
                    "INSERT INTO episode_music_bindings VALUES (?,?,?,?)",
                    (
                        episode.episode_id,
                        request["request_id"],
                        adapter.identity.artifact_id,
                        self.config.music_generation.base_url,
                    ),
                )
                db.commit()
            # Re-enter via the authoritative binding, also handling prepare-before-bind crashes.
            return self._music(episode, inputs)
        if result.get("status") != "succeeded":
            status = (
                "PENDING_PROVIDER"
                if result.get("status") == "pending_provider"
                or result.get("action") == "existing_interaction_pending"
                else "AMBIGUOUS"
                if result.get("status") in {"ambiguous", "remote_started"}
                else "FAILED"
            )
            raise ProductionStop(
                status,
                "Music has not completed; no new candidate was generated",
                {
                    **result,
                    "recovery_action": "resume_music_task"
                    if status in {"AMBIGUOUS", "PENDING_PROVIDER"}
                    and benchmark.request(request["request_id"])["status"] == "ambiguous"
                    and benchmark.request(request["request_id"])["provider_request_id"]
                    else None,
                },
            )
        request_id, blind_id = str(result["request_id"]), str(result["blind_id"])
        request = benchmark.request(request_id)
        with closing(self.database.connect()) as db:
            output = db.execute(
                "SELECT * FROM music_outputs WHERE request_id=?", (request_id,)
            ).fetchone()
        assert output is not None
        path = self.config.data_root / "music-benchmark" / output["relative_path"]
        persist_file(
            self.store,
            episode,
            "audio_master",
            "main",
            path,
            (adapter.identity.artifact_id,),
            Provenance(
                source_kind="provider",
                acquired_at=datetime.now(UTC),
                provider=request["provider"],
                model=request["model"],
                request_id=request["provider_request_id"],
                local_request_id=request_id,
                prompt_version="creative_music_v1",
                input_artifact_ids=(adapter.identity.artifact_id,),
            ),
        )
        return request_id, blind_id

    def _analysis(self, episode: Episode, blind_id: str) -> dict[str, Any]:
        config = self.config.automation
        benchmark = MusicBenchmark(self.database, self.config.data_root / "music-benchmark")
        report, reused = benchmark.analyze_audio(
            blind_id,
            config.analysis_version,
            AnalysisConfig(
                asr_model=config.asr_model,
                device=config.analysis_device,
                allow_model_download=config.allow_model_download,
            ),
        )
        with closing(self.database.connect()) as db:
            qa_row = db.execute(
                "SELECT * FROM music_policy_evaluations WHERE subject_type='audio' AND "
                "subject_id=? AND policy_id='music_qa' ORDER BY rowid DESC LIMIT 1",
                (blind_id,),
            ).fetchone()
            timing_row = db.execute(
                "SELECT * FROM music_policy_evaluations WHERE subject_type='timing' AND "
                "subject_id=? AND policy_id='music_timing' ORDER BY rowid DESC LIMIT 1",
                (f"{blind_id}:{config.analysis_version}",),
            ).fetchone()
        analysis_hash = sha256(report.model_dump_json().encode()).hexdigest()
        timing_hash = sha256(
            json.dumps(
                report.timing.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        qa_cached = bool(
            qa_row
            and qa_row["evaluator_type"] == "machine"
            and qa_row["subject_sha256"] == report.audio_sha256
            and json.loads(qa_row["evidence_json"]).get("analysis_sha256") == analysis_hash
        )
        timing_cached = bool(
            timing_row
            and timing_row["evaluator_type"] == "machine"
            and timing_row["subject_sha256"] == report.audio_sha256
            and json.loads(timing_row["evidence_json"]).get("analysis_sha256") == timing_hash
        )
        qa = (
            dict(qa_row)
            if qa_cached and qa_row
            else benchmark.evaluate_analysis_qa(blind_id, config.analysis_version)
        )
        timing = (
            dict(timing_row)
            if timing_cached and timing_row
            else benchmark.evaluate_timing(blind_id, config.analysis_version)
        )
        evidence = {
            "analysis_version": report.version,
            "audio_sha256": report.audio_sha256,
            "qa": qa,
            "timing": timing,
            "reused": reused,
            "analysis_warnings": report.warnings,
            "transcription_failure": report.transcription.failure_reason,
            "alignment_failure": report.alignment.failure_reason,
            "rhythm_failure": report.rhythm.failure_reason,
        }
        if qa["status"] != "pass" or timing["status"] != "pass":
            raise ProductionStop(
                "BLOCKED", "Objective audio/lyric/timing QA did not pass", evidence
            )
        return evidence
