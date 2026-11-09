"""Deterministic financial value normalization (no FX guessing)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class NormalizedValue:
    raw_value: str
    original_unit: str | None
    normalized_amount: float | None
    normalized_currency: str | None
    currency_conversion_available: bool
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_value": self.raw_value,
            "original_unit": self.original_unit,
            "normalized_amount": self.normalized_amount,
            "normalized_currency": self.normalized_currency,
            "currency_conversion_available": self.currency_conversion_available,
            "note": self.note,
        }


_MULTIPLIERS = {
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
    "cr": 10_000_000,
    "million": 1_000_000,
    "mn": 1_000_000,
    "m": 1_000_000,
    "billion": 1_000_000_000,
    "bn": 1_000_000_000,
    "b": 1_000_000_000,
    "k": 1_000,
    "thousand": 1_000,
}

_CURRENCY_HINTS = {
    "inr": "INR",
    "₹": "INR",
    "rs": "INR",
    "usd": "USD",
    "$": "USD",
    "eur": "EUR",
    "€": "EUR",
}


def _detect_currency(text: str) -> str | None:
    lower = text.lower()
    for hint, code in _CURRENCY_HINTS.items():
        if hint in lower:
            return code
    return None


def _parse_number(num_str: str) -> float | None:
    cleaned = num_str.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def normalize_financial_value(raw: str) -> NormalizedValue:
    """
    Normalize mixed formats like ₹5 Cr, INR 50 lakh, $2M, USD 1.5 million.
    Does not guess FX rates between currencies.
    """
    text = raw.strip()
    if not text:
        return NormalizedValue(
            raw_value=raw,
            original_unit=None,
            normalized_amount=None,
            normalized_currency=None,
            currency_conversion_available=False,
            note="empty input",
        )

    currency = _detect_currency(text)
    lower = text.lower()

    # Pattern: number + optional unit word
    match = re.search(
        r"([₹$€]?\s*[\d,]+(?:\.\d+)?)\s*(crore|crores|cr|lakh|lakhs|lac|lacs|million|mn|billion|bn|thousand|k|m|b)?",
        lower,
        re.IGNORECASE,
    )
    if not match:
        return NormalizedValue(
            raw_value=raw,
            original_unit=None,
            normalized_amount=None,
            normalized_currency=currency,
            currency_conversion_available=False,
            note="could not parse numeric value",
        )

    num_part = match.group(1)
    unit_part = (match.group(2) or "").lower()
    base = _parse_number(re.sub(r"[₹$€]", "", num_part))
    if base is None:
        return NormalizedValue(
            raw_value=raw,
            original_unit=unit_part or None,
            normalized_amount=None,
            normalized_currency=currency,
            currency_conversion_available=False,
            note="invalid number",
        )

    multiplier = _MULTIPLIERS.get(unit_part, 1.0)
    # Disambiguate bare 'm' — only treat as million if near currency or explicit
    if unit_part == "m" and "million" not in lower and "$" not in text and "usd" not in lower:
        multiplier = 1.0

    amount = base * multiplier

    return NormalizedValue(
        raw_value=raw,
        original_unit=unit_part or None,
        normalized_amount=amount,
        normalized_currency=currency,
        currency_conversion_available=bool(currency),
        note=None if currency else "currency unknown; no FX conversion applied",
    )


def normalize_financial_dict(values: dict[str, str | None]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for key, val in values.items():
        if val:
            result[key] = normalize_financial_value(val).to_dict()
    return result
