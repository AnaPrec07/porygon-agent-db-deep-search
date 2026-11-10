"""Quote verification: a fact is kept only when its quote appears in the page text the model saw.

Matching is tried in order: exact substring, then after normalization (Unicode NFKC, whitespace collapsed,
case folded), then a fuzzy window match at or above ``FUZZY_THRESHOLD``. ``rapidfuzz`` is not a dependency yet,
so the fuzzy step uses ``difflib`` from the standard library.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

FUZZY_THRESHOLD = 0.9


@dataclass(frozen=True)
class QuoteCheck:
    verified: bool
    method: str  # exact | normalized | fuzzy | none
    score: float


def normalize_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


def verify_quote(quote: str, source_text: str) -> QuoteCheck:
    if not quote.strip() or not source_text.strip():
        return QuoteCheck(False, "none", 0.0)
    if quote in source_text:
        return QuoteCheck(True, "exact", 1.0)
    norm_quote = normalize_text(quote)
    norm_source = normalize_text(source_text)
    if norm_quote and norm_quote in norm_source:
        return QuoteCheck(True, "normalized", 1.0)
    if not norm_quote or len(norm_quote) > len(norm_source):
        return QuoteCheck(False, "none", 0.0)
    width = len(norm_quote)
    last_start = len(norm_source) - width

    def ratio_at(start: int) -> float:
        window = norm_source[start : start + width]
        return SequenceMatcher(None, norm_quote, window, autojunk=False).ratio()

    # Coarse pass to find the neighbourhood, then a fine pass around the best start.
    coarse = max(1, width // 4)
    best_start, best = 0, -1.0
    for start in range(0, last_start + 1, coarse):
        ratio = ratio_at(start)
        if ratio > best:
            best_start, best = start, ratio
    for start in range(max(0, best_start - coarse), min(last_start, best_start + coarse) + 1):
        ratio = ratio_at(start)
        if ratio > best:
            best = ratio
    if best >= FUZZY_THRESHOLD:
        return QuoteCheck(True, "fuzzy", round(best, 3))
    return QuoteCheck(False, "none", round(best, 3))
