"""Configurable LLM inference with retry, timeout, and safe fallback."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

from env_config import load_env

load_env()

logger = logging.getLogger(__name__)

MODEL_RETRY_LIMIT = 3
DEFAULT_TIMEOUT = int(os.environ.get("MODEL_TIMEOUT_SECONDS", "120"))


@dataclass
class InferenceResult:
    content: str
    provider: str
    model: str
    attempts: int
    fallback_used: bool = False


def _get_provider() -> str:
    return os.environ.get("MODEL_PROVIDER", "ollama").lower()


def _call_ollama(
    prompt: str,
    model: str,
    json_schema: dict | None,
    timeout: int,
    base_url: str | None = None,
) -> str:
    import ollama

    host = base_url or os.environ.get("OLLAMA_BASE_URL")
    client = (
        ollama.Client(host=host, timeout=timeout)
        if host
        else ollama.Client(timeout=timeout)
    )
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0.2},
    }
    if json_schema:
        kwargs["format"] = json_schema
    response = client.chat(**kwargs)
    return response.message.content


def _call_openai_compatible(prompt: str, model: str, timeout: int) -> str:
    """Optional provider: any OpenAI-compatible endpoint (requires `pip install openai`)."""
    from openai import OpenAI

    client = OpenAI(
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
        api_key=os.environ.get("OPENAI_API_KEY", "not-needed"),
        timeout=timeout,
    )
    resp = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", model),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return resp.choices[0].message.content or ""


def run_inference(
    prompt: str,
    *,
    model: str | None = None,
    json_schema: dict | None = None,
    timeout: int | None = None,
    retry_limit: int = MODEL_RETRY_LIMIT,
    fallback_fn: Callable[[], str] | None = None,
) -> InferenceResult:
    """
    Run inference with exactly `retry_limit` attempts (default 3).
    On total failure, invoke fallback_fn if provided.
    """
    provider = _get_provider()
    timeout = timeout or DEFAULT_TIMEOUT
    model = model or os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
    last_error: Exception | None = None

    for attempt in range(1, retry_limit + 1):
        try:
            logger.info(
                "inference_attempt",
                extra={"provider": provider, "attempt": attempt, "model": model},
            )
            if provider == "ollama":
                content = _call_ollama(prompt, model, json_schema, timeout)
            else:
                content = _call_openai_compatible(prompt, model, timeout)
            return InferenceResult(
                content=content,
                provider=provider,
                model=model,
                attempts=attempt,
            )
        except Exception as exc:
            last_error = exc
            logger.warning("Inference attempt %d failed: %s", attempt, exc)
            if attempt < retry_limit:
                time.sleep(min(2 ** attempt, 8))

    if fallback_fn is not None:
        logger.warning("All inference attempts failed; using fallback mode")
        return InferenceResult(
            content=fallback_fn(),
            provider=provider,
            model=model,
            attempts=retry_limit,
            fallback_used=True,
        )

    raise RuntimeError(f"Inference failed after {retry_limit} attempts: {last_error}")


def run_inference_with_fallback(
    prompt: str,
    *,
    json_schema: dict | None = None,
    timeout: int | None = None,
    retry_limit: int = MODEL_RETRY_LIMIT,
) -> InferenceResult:
    """
    Primary path: high-fidelity Ollama host (e.g. a larger model on a GPU box).
    Fallback path: local Mistral, so every deal still gets a response.
    """
    primary_url = os.environ.get("OLLAMA_PRIMARY_BASE_URL") or os.environ.get("OLLAMA_BASE_URL")
    primary_model = os.environ.get("OLLAMA_PRIMARY_MODEL") or os.environ.get("OLLAMA_MODEL", "llama3:70b")
    fallback_url = os.environ.get("OLLAMA_FALLBACK_BASE_URL", "http://127.0.0.1:11434")
    fallback_model = os.environ.get("OLLAMA_FALLBACK_MODEL", "mistral")
    primary_timeout = int(os.environ.get("OLLAMA_PRIMARY_TIMEOUT", "20"))

    try:
        return _run_inference_on_host(
            prompt,
            base_url=primary_url,
            model=primary_model,
            json_schema=json_schema,
            timeout=primary_timeout,
            retry_limit=min(retry_limit, 2),
            label="primary",
        )
    except Exception as primary_err:
        logger.warning("Primary inference failed (%s); falling back to local model", primary_err)
        result = _run_inference_on_host(
            prompt,
            base_url=fallback_url,
            model=fallback_model,
            json_schema=json_schema,
            timeout=timeout,
            retry_limit=retry_limit,
            label="fallback",
        )
        result.fallback_used = True
        return result


def _run_inference_on_host(
    prompt: str,
    *,
    base_url: str | None,
    model: str,
    json_schema: dict | None,
    timeout: int | None,
    retry_limit: int,
    label: str,
) -> InferenceResult:
    timeout = timeout or DEFAULT_TIMEOUT
    last_error: Exception | None = None
    for attempt in range(1, retry_limit + 1):
        try:
            content = _call_ollama(prompt, model, json_schema, timeout, base_url=base_url)
            return InferenceResult(
                content=content,
                provider=f"ollama-{label}",
                model=model,
                attempts=attempt,
            )
        except Exception as exc:
            last_error = exc
            logger.warning("%s inference attempt %d failed: %s", label, attempt, exc)
            if attempt < retry_limit:
                time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"{label} inference failed after {retry_limit} attempts: {last_error}")
