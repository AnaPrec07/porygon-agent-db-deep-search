"""Citation rewriting, ported from the recipe's citation_replacement_callback.

The composer writes <cite source="src-N" /> tags. Known ids become Markdown links; unknown ids are dropped
(never rendered as broken links). Spacing before punctuation is then normalized, as in the original.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

CITE_PATTERN = re.compile(r'<cite\s+source\s*=\s*["\']?\s*(src-\d+)\s*["\']?\s*/>')
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:])")


def replace_citations(report: str, sources: dict[str, dict[str, Any]]) -> tuple[str, dict[str, int]]:
    """Return the report with citations resolved, plus counts of total, valid and dropped tags."""
    stats = {"total": 0, "valid": 0, "dropped": 0}

    def _replace(match: re.Match[str]) -> str:
        stats["total"] += 1
        short_id = match.group(1)
        info = sources.get(short_id)
        if not info:
            stats["dropped"] += 1
            logger.warning("Invalid citation tag removed: %s", match.group(0))
            return ""
        stats["valid"] += 1
        display = info.get("title") or info.get("domain") or short_id
        return f" [{display}]({info['url']})"

    text = CITE_PATTERN.sub(_replace, report)
    return _SPACE_BEFORE_PUNCT.sub(r"\1", text), stats
