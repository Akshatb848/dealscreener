#!/usr/bin/env python3
"""PostgreSQL compatibility: schema init against postgres when POSTGRES_TEST_URL is set."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect

from models import Base


@pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"),
    reason="Set POSTGRES_TEST_URL to run PostgreSQL compatibility tests",
)
def test_postgres_schema_init():
    url = os.environ["POSTGRES_TEST_URL"]
    engine = create_engine(url, pool_pre_ping=True)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert "companies" in tables
    assert "audit_logs" in tables


def test_sqlite_schema_still_valid():
    from db import init_db

    init_db("sqlite:///:memory:")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    assert "job_runs" in inspect(engine).get_table_names()
    engine.dispose()
