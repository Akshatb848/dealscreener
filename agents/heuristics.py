"""Deterministic text heuristics shared by the advanced agents.

These are evidence-grounded, reproducible helpers used as the MVP substitute for
live LLM calls. They never fabricate facts: a signal is only emitted when its
trigger terms actually appear in the source chunks. Every agent keeps a clean
extension point to swap in a real LLM (see ``agents.run_agent_inference``).
"""

from __future__ import annotations

from agent_schemas import EvidenceItem
from parser_layer import ParsedChunk


def joined_text(chunks: list[ParsedChunk]) -> str:
    return "\n".join(c.raw_text for c in chunks)


def lower_text(chunks: list[ParsedChunk]) -> str:
    return joined_text(chunks).lower()


def find_terms(text_lower: str, terms: list[str]) -> list[str]:
    """Return the subset of ``terms`` present in the text (stable order)."""
    return [t for t in terms if t.lower() in text_lower]


def first_chunk_evidence(
    chunks: list[ParsedChunk], note: str, *, terms: list[str] | None = None
) -> EvidenceItem | None:
    """Build an evidence item anchored to the first chunk that contains a term."""
    if not chunks:
        return None
    target = chunks[0]
    if terms:
        for c in chunks:
            low = c.raw_text.lower()
            if any(t.lower() in low for t in terms):
                target = c
                break
    return EvidenceItem(
        chunk_id=f"{target.source_file}:{target.page_index}",
        source_file=target.source_file,
        page_index=target.page_index,
        quoted_or_paraphrased_evidence=note,
        confidence_score=target.confidence_score or 0.5,
    )


def presence_score(text_lower: str, positive: list[str], negative: list[str] | None = None) -> float:
    """Deterministic 0.0–1.0 score: base 0.4, +0.6 weighted by positive-term hits,
    minus penalty for negative-term hits. Reproducible for identical input."""
    negative = negative or []
    pos_hits = sum(1 for t in positive if t.lower() in text_lower)
    neg_hits = sum(1 for t in negative if t.lower() in text_lower)
    if not positive:
        base = 0.5
    else:
        base = 0.4 + 0.6 * (pos_hits / len(positive))
    base -= 0.15 * neg_hits
    return round(max(0.0, min(1.0, base)), 4)
