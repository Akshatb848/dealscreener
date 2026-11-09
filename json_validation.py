"""Structured JSON verification with a single repair attempt."""

from __future__ import annotations

import json
import logging
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class JSONValidationError(Exception):
    """Raised when JSON cannot be validated even after repair."""

    def __init__(self, message: str, raw: str | None = None):
        super().__init__(message)
        self.raw = raw


def _extract_json_object(text: str) -> str:
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def validate_or_repair_json(
    raw: str,
    schema: Type[T],
    repair_fn=None,
) -> tuple[T | None, bool]:
    """
    Validate model output against schema.
    Returns (validated_model, needs_review).
    On invalid JSON: one repair attempt via repair_fn or json.loads after extraction.
    """
    try:
        data = json.loads(_extract_json_object(raw))
        return schema.model_validate(data), False
    except (json.JSONDecodeError, ValidationError) as first_err:
        logger.warning("Initial JSON validation failed: %s", first_err)

    # Exactly one repair attempt
    try:
        if repair_fn is not None:
            repaired = repair_fn(raw)
        else:
            repaired = _extract_json_object(raw)
        data = json.loads(repaired)
        return schema.model_validate(data), False
    except (json.JSONDecodeError, ValidationError) as second_err:
        logger.error("JSON repair failed: %s", second_err)
        return None, True
