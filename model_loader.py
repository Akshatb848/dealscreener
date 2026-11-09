"""Runtime model loader with hotswap, rollback, and safe deterministic fallback."""

from __future__ import annotations

import logging
import os
import pickle
import threading
from typing import Any

from sqlalchemy.orm import Session

from model_registry import (
    get_active_model_version,
    get_rollback_model_version,
)

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_loaded_model: Any = None
_loaded_version: str | None = None
_safe_mode: bool = False


class SafeDeterministicClassifier:
    """Fallback when no model can be loaded — uses scoring_logic only."""

    def predict_proba(self, X):
        import numpy as np

        n = len(X) if hasattr(X, "__len__") else 1
        # Neutral 0.5 accept probability
        return np.array([[0.5, 0.5]] * n)


def _load_pickle(path: str) -> Any:
    with open(path, "rb") as f:
        return pickle.load(f)


def load_active_model(session: Session, force_reload: bool = False) -> tuple[Any, str | None]:
    """
    Load active model dynamically. Falls back to rollback, then safe deterministic mode.
    """
    global _loaded_model, _loaded_version, _safe_mode

    with _lock:
        active = get_active_model_version(session)
        version_label = active.version_label if active else None

        if not force_reload and _loaded_model is not None and _loaded_version == version_label:
            return _loaded_model, _loaded_version

        candidates = []
        if active and os.path.isfile(active.model_path):
            candidates.append((active.version_label, active.model_path))

        rollback = get_rollback_model_version(session)
        if rollback and os.path.isfile(rollback.model_path):
            candidates.append((rollback.version_label, rollback.model_path))

        # Env fallback path
        fallback_path = os.environ.get("CLASSIFIER_MODEL_PATH", "models/classifier.pkl")
        if os.path.isfile(fallback_path):
            candidates.append(("env_fallback", fallback_path))

        for label, path in candidates:
            try:
                model = _load_pickle(path)
                _loaded_model = model
                _loaded_version = label
                _safe_mode = False
                logger.info("Loaded model version %s from %s", label, path)
                return model, label
            except Exception as exc:
                logger.error("Failed to load model %s: %s", label, exc)

        logger.warning("Entering safe deterministic mode — no model loadable")
        _loaded_model = SafeDeterministicClassifier()
        _loaded_version = "safe_deterministic"
        _safe_mode = True
        return _loaded_model, _loaded_version


def reload_active_model(session: Session) -> tuple[Any, str | None]:
    return load_active_model(session, force_reload=True)


def is_safe_mode() -> bool:
    return _safe_mode
