"""FTS5-based retrieval for semantic facts."""
from __future__ import annotations

import re


def build_fts5_query(question: str) -> str:
    """Convert a natural-language question into an FTS5 query string.

    Strips common stop words and punctuation, joins remaining tokens with OR
    for broad matching.
    """
    stop_words = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "can", "shall",
        "of", "in", "to", "for", "with", "on", "at", "from", "by",
        "about", "as", "into", "through", "during", "before", "after",
        "it", "its", "this", "that", "these", "those", "i", "me", "my",
        "we", "our", "you", "your", "he", "she", "they", "them",
        "what", "which", "who", "whom", "how", "many", "much",
        "and", "or", "but", "not", "no", "so", "if", "then",
    }

    tokens = re.findall(r'\b\w+\b', question.lower())
    meaningful = [t for t in tokens if t not in stop_words and len(t) > 1]

    if not meaningful:
        meaningful = [t for t in tokens if len(t) > 1]

    if not meaningful:
        return question.strip()

    return " OR ".join(meaningful)
