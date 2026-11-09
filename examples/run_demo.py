#!/usr/bin/env python3
"""End-to-end demo: screen the synthetic pitches in examples/pitches with a mocked LLM.

No Ollama, Redis, PostgreSQL or Docker needed. The script:

1. points the app at a throwaway SQLite database,
2. replaces the LLM call with the deterministic mock in ``examples/mock_llm.py``,
3. uploads each pitch through the real FastAPI endpoint (``POST /api/v1/upload``),
4. prints the verdict table and writes each IC memo to ``examples/output/``.

Run from the repository root::

    python examples/run_demo.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PITCHES = Path(__file__).resolve().parent / "pitches"
OUTPUT = Path(__file__).resolve().parent / "output"


def _configure_env(db_path: str, storage: str) -> None:
    os.environ.update(
        {
            "DATABASE_URL": f"sqlite:///{db_path}",
            "STORAGE_DIR": storage,
            "RUN_LIVE_LLM": "true",          # agents call the (mocked) LLM
            "ASYNC_JOBS": "false",           # evaluate inline instead of via the worker
            "REDIS_URL": "",                 # no Redis needed for the demo
            "REDIS_REQUIRED": "false",
            "ENABLE_ADVANCED_PIPELINE": "true",
            "SIMULATION_ITERATIONS": "100",
            "API_KEY": "",
            "LOG_LEVEL": "WARNING",
        }
    )


def _memo_markdown(memo: dict) -> str:
    sc = memo["scorecard"]
    lines = [
        f"# {memo['title']}",
        "",
        f"**Recommendation:** {memo['recommendation']}  ",
        f"**Deterministic score:** {sc['final_score']:.1f} (scoring v{sc['scoring_version']})  ",
        f"**Decision authority:** {memo['decision_authority']}",
        "",
        "## Executive summary", memo["executive_summary"], "",
        "## Business thesis", memo["business_thesis"], "",
        "## Market & moat", memo["market_and_moat"], "",
        "## Financials", memo["financials"], "",
        "## Founders", memo["founder_assessment"], "",
        "## ESG & governance", memo["esg_and_governance"], "",
        "## Key risks", *[f"- {r}" for r in memo["key_risks"] or ["None flagged"]], "",
        "## Review flags", *[f"- {r}" for r in memo["review_flags"] or ["None"]], "",
        f"_{memo['disclaimer']}_", "",
    ]
    return "\n".join(lines)


def main() -> int:
    sys.path.insert(0, str(ROOT))
    tmp = tempfile.mkdtemp(prefix="dealscreener-demo-")
    _configure_env(os.path.join(tmp, "demo.db"), os.path.join(tmp, "storage"))

    from examples.mock_llm import install_mock_llm

    install_mock_llm()

    from api_app import app
    from db import init_db
    from tests.asgi_client import SyncASGIClient

    init_db()
    client = SyncASGIClient(app)
    OUTPUT.mkdir(exist_ok=True)

    rows = []
    for pitch in sorted(PITCHES.glob("*.md")):
        name = pitch.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").split(" — ")[0]
        with pitch.open("rb") as fh:
            r = client.post(
                "/api/v1/upload",
                data={"company_name": name},
                files={"file": (pitch.name, fh, "text/markdown")},
            )
        r.raise_for_status()
        up = r.json()
        evaluation = client.get(f"/api/v1/evaluation/{up['evaluation_id']}").json()
        deal = client.get(f"/api/v1/deals/{up['company_id']}").json()
        memo_resp = client.get(f"/api/v1/deals/{up['company_id']}/ic-memo")
        if memo_resp.status_code == 200:
            memo = memo_resp.json()
            (OUTPUT / f"{pitch.stem}_ic_memo.md").write_text(_memo_markdown(memo), encoding="utf-8")
            (OUTPUT / f"{pitch.stem}_ic_memo.json").write_text(json.dumps(memo, indent=2), encoding="utf-8")
        sim = deal.get("simulation") or {}
        rows.append(
            (
                name,
                evaluation["recommendation"],
                evaluation["deterministic_score"],
                len(evaluation["missing_information"]),
                sim.get("resilience_score"),
                deal.get("open_review_items", 0),
            )
        )

    print()
    print(f"{'Company':34} {'Verdict':13} {'Score':>6} {'Missing':>8} {'Resilience':>11} {'Reviews':>8}")
    print("-" * 86)
    for name, verdict, score, missing, resilience, reviews in rows:
        res = f"{resilience:.1f}" if resilience is not None else "-"
        print(f"{name:34} {verdict:13} {score:6.1f} {missing:8d} {res:>11} {reviews:8d}")
    print()
    print(f"IC memos written to {OUTPUT.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
