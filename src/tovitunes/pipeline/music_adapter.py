"""Lossless deterministic adapter from selected creative evidence to canonical music."""

from tovitunes.creative.validation import validate_episode_spec, validate_lyrics, validate_music
from tovitunes.domain.creative import EpisodeSpec, LyricsSpec, MusicSpec
from tovitunes.domain.episode import Episode
from tovitunes.music.models import CanonicalMusicSpec, LyricCandidate, LyricLine, MusicBrief


def creative_music_spec(
    episode: Episode,
    spec: EpisodeSpec,
    lyrics: LyricsSpec,
    music: MusicSpec,
    creative_ids: tuple[str, str, str],
) -> CanonicalMusicSpec:
    validate_episode_spec(episode, spec)
    validate_lyrics(episode, lyrics, creative_ids[0])
    validate_music(episode, music, creative_ids[1])
    duration = music.target_duration_seconds
    bpm = music.tempo_bpm or 112
    brief_id = "episode_" + episode.episode_id.replace("-", "_")
    brief = MusicBrief(
        schema_version=1,
        id=brief_id,
        episode_key=episode.external_key,
        objective=episode.objective,
        examples=episode.target_vocabulary,
        language="en",
        age_min=3,
        age_max=6,
        preferred_duration_seconds=(duration, duration),
        maximum_duration_seconds=45,
        target_bpm=bpm,
        bpm_range=(max(60, bpm - 12), min(180, bpm + 12)),
        style=music.mood,
        arrangement=(
            "; ".join(music.instrumentation)
            + ". Requested music structure: "
            + "; ".join(
                f"{section.name} {section.target_seconds:g} seconds" for section in music.sections
            )
        ),
        vocal_direction=music.vocal_direction,
        sections=tuple(dict.fromkeys(section.name for section in music.sections)),
        avoid=("named artist imitation", "extra lyrics", "unsafe preschool content"),
        candidate_count_per_provider=1,
    )
    candidate = LyricCandidate(
        schema_version=1,
        id=creative_ids[1],
        brief_id=brief_id,
        approval="pending",
        educational_claims=(episode.objective,),
        rhyme_notes="Exact selected LyricsSpec",
        syllable_notes="Preserve supplied words and line order",
        lines=tuple(LyricLine(section=line.section, text=line.text) for line in lyrics.lines),
    )
    return CanonicalMusicSpec(brief=brief, lyrics=candidate, attempt=1)
