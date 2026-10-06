"""Conservative V1 structural and lexical checks; not a semantic safety certification."""

import re
from collections import Counter
from collections.abc import Sequence

from tovitunes.creative.models import CreativeSubjectCandidate, EpisodePublicationMetadata
from tovitunes.creative.similarity import lexical_similarity, normalize_topic
from tovitunes.domain.creative import EpisodeSpec, LyricsSpec, MusicSpec
from tovitunes.domain.episode import Episode

# Familiar physical examples keep V1 treatment concrete. New examples require a policy version.
FAMILIAR_OBJECTS = (
    "apple",
    "ball",
    "block",
    "book",
    "crayon",
    "cup",
    "flower",
    "leaf",
    "banana",
    "orange",
    "grape",
    "grapes",
    "strawberry",
    "carrot",
    "pumpkin",
    "toy",
    "sock",
    "hat",
    "shirt",
    "shoe",
    "shoes",
    "teddy",
    "teddy bear",
    "cloud",
    "rainbow",
    "paper",
    "ribbon",
    "kite",
    "paint",
    "swatch",
    "color swatch",
    "colour swatch",
    "sun",
)
PROHIBITED = (
    "sexual",
    "sex",
    "naked",
    "violence",
    "violent",
    "kill",
    "blood",
    "weapon",
    "weapons",
    "gun",
    "guns",
    "knife",
    "knives",
    "bomb",
    "threat",
    "scary",
    "frightening",
    "monster",
    "ghost",
    "drug",
    "drugs",
    "alcohol",
    "beer",
    "wine",
    "smoke",
    "smoking",
    "gambling",
    "casino",
    "political",
    "election",
    "president",
    "celebrity",
    "adult themes",
    "cigarettes",
    "peppa",
    "cocomelon",
    "disney",
    "paw patrol",
    "bluey",
    "spongebob",
    "mickey",
    "baby shark",
    "ask your parents to buy",
    "subscribe",
    "like and subscribe",
    "buy now",
    "youtube",
    "jump off",
    "run into the road",
    "cross the road alone",
    "touch the stove",
    "eat paint",
    "put in your mouth",
    "hold your breath",
    "climb onto",
    "play with fire",
    "electric socket",
    "new permanent character",
    "new character",
    "tovi's friend",
    "toviÃ¢â‚¬â„¢s friend",
    "physics",
    "quantum",
    "molecule",
    "politics",
    "sarcasm",
)


def contains(text: str, phrase: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text, re.IGNORECASE))


def safe_text(text: str, *, metadata: bool = False) -> None:
    for term in PROHIBITED:
        if metadata and term == "youtube":
            continue
        if contains(text, term):
            raise ValueError(f"creative safety policy rejects {term!r}")


def treatment(candidate: CreativeSubjectCandidate) -> str:
    return " ".join((candidate.premise, candidate.hook, *candidate.example_objects))


def treatment_signature(text: str) -> str:
    """Small lexical adaptation for obvious find/discover paraphrases, without embeddings."""
    synonyms = {
        "finds": "find",
        "found": "find",
        "discovers": "find",
        "discover": "find",
        "discovered": "find",
        "spots": "find",
        "spot": "find",
    }
    filler = {"a", "an", "the", "that", "is", "was", "it", "and", "tovi"}
    return " ".join(synonyms.get(w, w) for w in normalize_topic(text).split() if w not in filler)


def validate_subject(
    candidate: CreativeSubjectCandidate,
    eligible: set[str],
    history: Sequence[str],
    allowed_examples: Sequence[str] = FAMILIAR_OBJECTS,
) -> None:
    if candidate.concept_id not in eligible:
        raise ValueError("subject concept is not eligible")
    safe_text(
        " ".join(
            (
                candidate.premise,
                candidate.hook,
                candidate.setting,
                candidate.song_angle,
                candidate.reason,
                *candidate.example_objects,
            )
        )
    )
    for example in candidate.example_objects:
        words = normalize_topic(example).split()
        full_example = " ".join(words)
        if words and words[0] == candidate.concept_id:
            words = words[1:]
        if " ".join(words) not in allowed_examples and full_example not in allowed_examples:
            raise ValueError("subject must use familiar concrete example objects")
    if any(
        max(
            lexical_similarity(treatment(candidate), prior),
            lexical_similarity(
                treatment_signature(treatment(candidate)), treatment_signature(prior)
            ),
        )
        >= 0.78
        for prior in history
    ):
        raise ValueError("duplicate recent creative treatment")


def validate_episode_spec(episode: Episode, spec: EpisodeSpec) -> None:
    if (
        spec.episode_id != episode.episode_id
        or spec.concept_id != episode.concept_id
        or spec.objective_id != episode.objective_id
        or spec.teaching_vocabulary != episode.target_vocabulary
        or spec.concept.cast != ("tovi",)
        or not set(spec.concept.cast).issubset(p.character_id for p in episode.character_packs)
    ):
        raise ValueError("episode spec differs from pinned learning objective or cast")
    purposes = [beat.purpose for beat in spec.story_beats]
    if set(purposes) != {"hook", "teach", "practice", "payoff"}:
        raise ValueError("story progression must include hook, teach, practice and payoff")
    order = {"hook": 0, "teach": 1, "practice": 2, "payoff": 3}
    if len(purposes) > 8 or purposes != sorted(purposes, key=order.__getitem__):
        raise ValueError("story progression must be short and ordered")
    taught = {
        w
        for b in spec.story_beats
        if b.purpose in {"teach", "practice"}
        for w in b.teaching_vocabulary
    }
    if not set(episode.target_vocabulary).issubset(taught) or any(
        not set(b.teaching_vocabulary).issubset(episode.target_vocabulary) for b in spec.story_beats
    ):
        raise ValueError("teaching beats must cover exactly the pinned vocabulary")
    safe_text(
        " ".join(
            (
                spec.concept.premise,
                spec.concept.hook,
                spec.concept.setting,
                spec.desired_structure,
                *(b.action for b in spec.story_beats),
            )
        )
    )


def validate_lyrics(episode: Episode, lyrics: LyricsSpec, spec_id: str) -> None:
    if (
        lyrics.episode_id != episode.episode_id
        or lyrics.objective_id != episode.objective_id
        or lyrics.episode_spec_artifact_id != spec_id
        or lyrics.target_vocabulary != episode.target_vocabulary
    ):
        raise ValueError("lyrics differ from selected objective or episode spec")
    texts = [line.text for line in lyrics.lines]
    if len(texts) > 16 or any(len(t) > 100 or len(t.split()) > 16 for t in texts):
        raise ValueError("lyrics exceed bounded line count or line length")
    if sum(len(t.split()) for t in texts) > episode.target_duration_seconds * 2:
        raise ValueError("lyrics exceed the target-duration word budget")
    if max(Counter(texts).values()) > 3:
        raise ValueError("lyrics have excessive identical-line repetition")
    if any(re.search(r"[\[\]()]", t) for t in texts):
        raise ValueError("lyrics cannot contain production directions")
    safe_text(" ".join(texts))


def validate_music(episode: Episode, music: MusicSpec, lyrics_id: str) -> None:
    if (
        music.episode_id != episode.episode_id
        or music.objective_id != episode.objective_id
        or music.lyrics_artifact_id != lyrics_id
        or music.target_vocabulary != episode.target_vocabulary
        or music.target_duration_seconds != episode.target_duration_seconds
    ):
        raise ValueError("music spec differs from selected lyrics or objective")
    if music.tempo_bpm is None or not 80 <= music.tempo_bpm <= 130:
        raise ValueError("preschool music brief requires a moderate 80-130 BPM tempo")
    safe_text(" ".join((music.mood, music.vocal_direction, *music.instrumentation)))


def validate_metadata(
    episode: Episode,
    metadata: EpisodePublicationMetadata,
    final_id: str,
    final_sha: str,
    curriculum_ids: Sequence[str],
    facts_text: str,
) -> None:
    if (
        metadata.episode_id != episode.episode_id
        or metadata.concept_id != episode.concept_id
        or metadata.final_render_artifact_id != final_id
        or metadata.final_render_sha256 != final_sha
    ):
        raise ValueError("metadata differs from episode or final render identity")
    text = " ".join((metadata.youtube_title, metadata.youtube_description, *metadata.tags))
    safe_text(text, metadata=True)
    if not any(contains(metadata.youtube_title, w) for w in episode.target_vocabulary):
        raise ValueError("metadata title must identify the pinned learning concept")
    if not contains(metadata.youtube_description, "ToviTunes"):
        raise ValueError("metadata description must identify ToviTunes")
    if sum(ord(c) > 0xFFFF for c in metadata.youtube_title) > 2:
        raise ValueError("metadata title contains excessive emoji")
    for concept in curriculum_ids:
        if (
            concept != episode.concept_id
            and contains(text, concept)
            and not contains(facts_text, concept)
        ):
            raise ValueError("metadata references another concept absent from the video")
    for obj in FAMILIAR_OBJECTS:
        if contains(text, obj) and not contains(facts_text, obj):
            raise ValueError("metadata references an object absent from authoritative facts")
    for claim in (
        "cure",
        "guaranteed",
        "genius",
        "iq",
        "medical",
        "best ever",
        "won't believe",
        "learn to read",
        "learn math",
        "counting",
        "numbers",
    ):
        if contains(text, claim) and not contains(facts_text, claim):
            raise ValueError("unsupported metadata claim")
