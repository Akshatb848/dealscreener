"""Base utilities for specialized agents."""

from __future__ import annotations

import json
from typing import Type, TypeVar

from pydantic import BaseModel

from agent_schemas import EvidenceItem
from inference_service import run_inference, run_inference_with_fallback
from json_validation import validate_or_repair_json
from parser_layer import ParsedChunk

T = TypeVar("T", bound=BaseModel)


def chunks_to_context(chunks: list[ParsedChunk]) -> str:
    parts = []
    for c in chunks:
        parts.append(f"[chunk_id={c.source_file}:{c.page_index}]\n{c.raw_text}")
    return "\n\n---\n\n".join(parts)


def build_evidence_from_chunks(chunks: list[ParsedChunk], quote: str, chunk_index: int = 0) -> EvidenceItem:
    chunk = chunks[chunk_index] if chunks else None
    if chunk is None:
        return EvidenceItem(
            chunk_id="unknown",
            source_file="unknown",
            page_index=0,
            quoted_or_paraphrased_evidence=quote,
            confidence_score=0.0,
        )
    return EvidenceItem(
        chunk_id=f"{chunk.source_file}:{chunk.page_index}",
        source_file=chunk.source_file,
        page_index=chunk.page_index,
        quoted_or_paraphrased_evidence=quote,
        confidence_score=chunk.confidence_score,
    )


def run_agent_inference(
    prompt: str,
    schema: Type[T],
    chunks: list[ParsedChunk],
    *,
    model: str | None = None,
) -> tuple[T | None, bool]:
    """Call inference and validate JSON. Returns (output, needs_review)."""
    full_prompt = (
        f"{prompt}\n\n"
        "Return ONLY valid JSON matching the required schema. "
        "Do not invent data not present in the source. "
        "List missing fields in missing_variables.\n\n"
        f"SOURCE CHUNKS:\n{chunks_to_context(chunks)}"
    )
    try:
        result = run_inference_with_fallback(full_prompt, json_schema=schema.model_json_schema())
    except RuntimeError:
        return None, True

    def repair_fn(raw: str) -> str:
        repair_prompt = f"Fix this JSON to match the schema. Return ONLY JSON:\n{raw}"
        repaired = run_inference_with_fallback(repair_prompt)
        return repaired.content

    return validate_or_repair_json(result.content, schema, repair_fn=repair_fn)


def parse_llm_dict(content: str) -> dict:
    return json.loads(content)
