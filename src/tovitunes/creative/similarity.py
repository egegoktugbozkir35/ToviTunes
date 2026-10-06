"""First-party donor normalization and lexical similarity; embeddings deliberately excluded."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Sequence
from difflib import SequenceMatcher

_NON_WORD = re.compile(r"[^\w]+", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def normalize_topic(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = _NON_WORD.sub(" ", normalized)
    return _WHITESPACE.sub(" ", normalized).strip()


def lexical_similarity(left: str, right: str) -> float:
    left_normalized = normalize_topic(left)
    right_normalized = normalize_topic(right)
    if not left_normalized or not right_normalized:
        return 0.0
    if left_normalized == right_normalized:
        return 1.0
    left_tokens = set(left_normalized.split())
    right_tokens = set(right_normalized.split())
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    sequence = SequenceMatcher(None, left_normalized, right_normalized).ratio()
    containment = len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))
    # Containment is useful for a title with a small subtitle/suffix added.
    containment_score = (
        containment
        * min(len(left_tokens), len(right_tokens))
        / max(len(left_tokens), len(right_tokens))
    )
    return max(jaccard, sequence, containment_score)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    denominator = math.sqrt(sum(v * v for v in left) * sum(v * v for v in right))
    return (
        sum(a * b for a, b in zip(left, right, strict=True)) / denominator if denominator else 0.0
    )
