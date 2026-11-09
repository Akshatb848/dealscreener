"""Deterministic stand-in for the Ollama LLM, so the pipeline runs without a model.

The real agents send a prompt + JSON schema to Ollama. This mock reads the
``SOURCE CHUNKS`` section of the prompt, extracts simple ``Key: value`` lines
(the format used by the example pitches in ``examples/pitches``) and returns
JSON that matches the requested schema. It is *not* an LLM — it exists so that
anyone can try the end-to-end flow (and CI can test it) with no GPU and no
model download.

Usage::

    from examples.mock_llm import install_mock_llm
    install_mock_llm()          # patches inference_service._call_ollama
"""

from __future__ import annotations

import json
import re
from typing import Any

_CHUNK_HEADER = re.compile(r"\[chunk_id=([^\]]+)\]")

# schema title -> {output_field: [pitch keys that populate it]}
_MARKET_KEYS = {
    "market_category": ["category"],
    "tam": ["tam"],
    "sam": ["sam"],
    "som": ["som"],
    "competitive_landscape": ["competition"],
}
_MARKET_LISTS = {"operational_moats": ["moat"], "gtm_signals": ["gtm"]}
_MARKET_REQUIRED = ["tam", "sam", "som", "market_category", "competitive_landscape"]

_FIN_KEYS = {
    "arr": ["arr"],
    "mrr": ["mrr"],
    "revenue": ["revenue"],
    "burn": ["burn"],
    "runway": ["runway"],
    "cac": ["cac"],
    "ltv": ["ltv"],
    "margins": ["gross margins", "margins"],
    "growth_rate": ["growth rate"],
    "funding_ask": ["funding ask"],
    "valuation": ["valuation"],
}
_FIN_REQUIRED = ["arr", "revenue", "burn", "runway", "growth_rate", "funding_ask"]


def _source_section(prompt: str) -> str:
    marker = "SOURCE CHUNKS:"
    return prompt.split(marker, 1)[1] if marker in prompt else prompt


def _key_values(source: str) -> tuple[dict[str, list[str]], str]:
    """Return {lower_key: [values]} for ``Key: value`` lines plus the first chunk id."""
    kv: dict[str, list[str]] = {}
    for line in source.splitlines():
        m = re.match(r"^\s*([A-Za-z][A-Za-z ]{1,30}):\s*(.+?)\s*$", line)
        if m:
            kv.setdefault(m.group(1).strip().lower(), []).append(m.group(2))
    header = _CHUNK_HEADER.search(source)
    return kv, (header.group(1) if header else "unknown:0")


def _evidence(chunk_id: str, text: str) -> dict[str, Any]:
    source_file, _, page = chunk_id.rpartition(":")
    return {
        "chunk_id": chunk_id,
        "source_file": source_file or chunk_id,
        "page_index": int(page) if page.isdigit() else 0,
        "quoted_or_paraphrased_evidence": text[:200],
        "confidence_score": 0.9,
    }


def _first(kv: dict[str, list[str]], keys: list[str]) -> str | None:
    for k in keys:
        if kv.get(k):
            return kv[k][0]
    return None


def _market(kv, chunk_id) -> dict:
    out: dict[str, Any] = {k: _first(kv, keys) for k, keys in _MARKET_KEYS.items()}
    for field, keys in _MARKET_LISTS.items():
        out[field] = [v for k in keys for v in kv.get(k, [])]
    out["defensibility_signals"] = list(out["operational_moats"])
    out["missing_variables"] = [f for f in _MARKET_REQUIRED if not out.get(f)]
    out["evidence"] = [_evidence(chunk_id, f"{k}: {v}") for k, v in out.items()
                       if isinstance(v, str) and v][:5]
    return out


def _financial(kv, chunk_id) -> dict:
    out: dict[str, Any] = {k: _first(kv, keys) for k, keys in _FIN_KEYS.items()}
    out["missing_variables"] = [f for f in _FIN_REQUIRED if not out.get(f)]
    out["evidence"] = [_evidence(chunk_id, f"{k}: {v}") for k, v in out.items()
                       if isinstance(v, str) and v][:5]
    return out


def _founder(kv, chunk_id) -> dict:
    names, backgrounds, exits = [], [], []
    for raw in kv.get("founder", []):
        name, _, bg = raw.partition(" — ")
        names.append(name.strip())
        if bg:
            backgrounds.append(bg.strip())
            if "exit" in bg.lower():
                exits.append(f"{name.strip()}: {bg.strip()}")
    out = {
        "founder_names": names,
        "founder_backgrounds": backgrounds,
        "prior_exits": exits,
        "hiring_team_strength": kv.get("hiring", []),
    }
    missing = []
    if not names:
        missing.append("founder_names")
    if not backgrounds:
        missing.append("founder_backgrounds")
    out["missing_variables"] = missing
    out["evidence"] = [_evidence(chunk_id, f"Founder: {n}") for n in names][:3]
    return out


_BUILDERS = {
    "MarketMoatAgentOutput": _market,
    "FinancialMetricsAgentOutput": _financial,
    "FounderMarketFitAgentOutput": _founder,
}


def mock_call_ollama(prompt: str, model: str, json_schema: dict | None, timeout: int,
                     base_url: str | None = None) -> str:
    """Drop-in replacement for ``inference_service._call_ollama``."""
    title = (json_schema or {}).get("title", "")
    kv, chunk_id = _key_values(_source_section(prompt))
    builder = _BUILDERS.get(title)
    if builder is None:
        # Repair prompts or unknown schemas: echo an empty JSON object.
        return "{}"
    return json.dumps(builder(kv, chunk_id))


def install_mock_llm() -> None:
    """Patch the inference layer so every agent call is served by the mock."""
    import inference_service

    inference_service._call_ollama = mock_call_ollama  # type: ignore[assignment]
