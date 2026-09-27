"""Fully offline NIM-contract fixture exercising the same durable structured path."""

import json
from collections.abc import Callable, Sequence
from typing import Any

from tovitunes.creative.models import CreativeSubjectCandidate, CreativeSubjectPool
from tovitunes.creative.provider import ChatResponse, Message
from tovitunes.domain.creative import EpisodeSpec, LyricsSpec
from tovitunes.domain.episode import Episode
from tovitunes.pipeline.creative import FakeDraftGenerator


class FakeNIMTransport:
    provider_name = "fake-nim"
    model_name = "offline-fixture-v1"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.messages: list[list[Message]] = []
        self.responses: dict[str, list[str | Exception]] = {}

    @property
    def settings(self) -> dict[str, object]:
        return {"fixture": "v1"}

    def check_ready(self) -> None:
        pass

    def chat(
        self, messages: Sequence[Message], *, record_identity: Callable[[str], None]
    ) -> ChatResponse:
        schema = json.loads(messages[0]["content"].split("JSON Schema:\n", 1)[1])
        kind = schema["title"]
        self.calls.append(kind)
        self.messages.append(list(messages))
        if self.responses.get(kind):
            value = self.responses[kind].pop(0)
            if isinstance(value, Exception):
                raise value
            return ChatResponse(value)
        facts: dict[str, Any] = json.loads(
            next(
                m["content"].split("Authoritative pinned facts:\n", 1)[1]
                for m in messages
                if m["content"].startswith("Authoritative pinned facts:\n")
            )
        )
        output: object
        if kind == "CreativeSubjectPool":
            concept = facts["eligible_concepts"][0]["concept_id"]
            objects = ("apple", "ball", "crayon")
            premises = (
                ("Tovi sorts", "on a tray", "Peek at"),
                ("Tovi rolls", "across a soft mat", "Watch a gentle roll from"),
                ("Tovi presents", "beside drawing paper", "A paper window reveals"),
            )
            offset = len(facts["used_concepts"]) % len(premises)
            candidates = [
                CreativeSubjectCandidate(
                    concept_id=concept,
                    premise=(
                        f"{premises[(i + offset) % 3][0]} a {concept} {obj} "
                        f"{premises[(i + offset) % 3][1]}."
                    ),
                    hook=f"{premises[(i + offset) % 3][2]} the {concept} {obj}.",
                    setting="a simple playroom",
                    example_objects=(obj,),
                    song_angle=f"Say {concept} clearly in a short song.",
                    reason="One concrete familiar object supports the pinned objective.",
                )
                for i, obj in enumerate(objects)
            ]
            output = CreativeSubjectPool(candidates=tuple(candidates)).model_dump(mode="json")
        elif kind == "EpisodePublicationMetadata":
            episode = facts["episode"]
            final = facts["final_render"]
            concept = episode["concept_id"]
            output = {
                "youtube_title": f"Learn {concept.title()} with Tovi | Kids Color Song #Shorts",
                "youtube_description": f"Learn {concept} with ToviTunes in this preschool song.",
                "tags": [concept, "preschool learning", "kids song", "shorts"],
                "language": "en",
                "made_for_kids": True,
                "episode_id": episode["episode_id"],
                "concept_id": concept,
                "final_render_artifact_id": final["artifact_id"],
                "final_render_sha256": final["sha256"],
            }
        else:
            episode = Episode.model_validate(facts["episode"])
            generator = FakeDraftGenerator()
            if kind == "EpisodeSpec":
                spec = generator.episode_spec(episode, variant=1).output
                if facts["selected_subject"]:
                    subject = facts["selected_subject"]
                    spec = spec.model_copy(
                        update={
                            "concept": spec.concept.model_copy(
                                update={
                                    "premise": subject["premise"],
                                    "hook": subject["hook"],
                                    "setting": subject["setting"],
                                }
                            )
                        }
                    )
                output = spec.model_dump(mode="json")
            elif kind == "LyricsSpec":
                output = generator.lyrics(
                    episode,
                    EpisodeSpec.model_validate(facts["episode_spec"]),
                    facts["episode_spec_artifact_id"],
                    variant=1,
                ).output.model_dump(mode="json")
            else:
                output = generator.music_spec(
                    episode,
                    LyricsSpec.model_validate(facts["lyrics"]),
                    facts["lyrics_artifact_id"],
                    variant=1,
                ).output.model_dump(mode="json")
        return ChatResponse(json.dumps(output))
