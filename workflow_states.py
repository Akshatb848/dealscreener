"""Explicit workflow states for ingestion and evaluation tracking."""

from enum import Enum


class WorkflowState(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PARSED = "parsed"
    AGENTS_RUNNING = "agents_running"
    SIMULATION_RUNNING = "simulation_running"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    COMPLETED = "completed"


VALID_WORKFLOW_STATES = frozenset(s.value for s in WorkflowState)


def is_valid_workflow_state(value: str) -> bool:
    return value in VALID_WORKFLOW_STATES
