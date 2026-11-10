"""Split a final report into index chunks (plan §E.6).

Chunk 0 is the abstract: the first paragraph under the title. Other chunks follow the markdown headings. A
section longer than ``max_chars`` is split on paragraph boundaries. Pure Python, so it runs and tests anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MAX_CHARS = 6000
ABSTRACT_MAX_CHARS = 1200


@dataclass(frozen=True)
class Chunk:
    index: int
    heading: str
    text: str


def _sections(markdown: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in markdown.splitlines():
        if line.lstrip().startswith("#"):
            sections.append((line.lstrip("#").strip(), []))
        else:
            sections[-1][1].append(line)
    return [(heading, "\n".join(body).strip()) for heading, body in sections if "\n".join(body).strip() or heading]


def _split_long(text: str, max_chars: int) -> list[str]:
    parts: list[str] = []
    current = ""
    for paragraph in (p.strip() for p in text.split("\n\n")):
        if not paragraph:
            continue
        if current and len(current) + len(paragraph) + 2 > max_chars:
            parts.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        parts.append(current)
    return parts


def chunk_report(markdown: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[Chunk]:
    text = (markdown or "").strip()
    if not text:
        return []
    chunks: list[Chunk] = []
    abstract = ""
    for paragraph in text.split("\n\n"):
        stripped = paragraph.strip()
        if stripped and not stripped.startswith("#"):
            abstract = stripped
            break
    chunks.append(Chunk(0, "abstract", abstract[:ABSTRACT_MAX_CHARS]))

    for heading, body in _sections(text):
        if abstract and body.startswith(abstract):
            # The abstract is the first paragraph under the title. It is already chunk 0, so do not index it twice.
            body = body[len(abstract) :].strip()
        if not body:
            continue
        pieces = _split_long(body, max_chars) if len(body) > max_chars else [body]
        for piece in pieces:
            chunks.append(Chunk(len(chunks), heading, piece))
    return chunks
