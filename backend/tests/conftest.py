from __future__ import annotations

import os
from pathlib import Path

import pytest

from starindex.config import reset_config_cache
from starindex.jobs.worker import Worker
from starindex.testing.harness import make_context

from .fixtures import build_fake

_ENV_KEYS = [
    "STARINDEX_DATA_DIR", "STARINDEX_DISABLE_KEYRING", "STARINDEX_GITHUB_API_URL", "STARINDEX_GITHUB_RAW_URL", "STARINDEX_HOST",
    "STARINDEX_PORT", "STARINDEX_ACCESS_TOKEN", "STARINDEX_QDRANT_URL", "STARINDEX_WORKER",
]


@pytest.fixture(autouse=True)
def _isolated_env():
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    reset_config_cache()


@pytest.fixture
def fake():
    return build_fake()


@pytest.fixture
def ctx(tmp_path: Path, fake):
    context = make_context(tmp_path / "data", fake, embedding="hashing")
    yield context
    context.close()


def run_jobs(context, **kw) -> int:
    """Drain the queue on the calling thread (no background threads in tests)."""
    return Worker(context, mode="embedded").drain(include_deferred=True, **kw)


@pytest.fixture
def synced(ctx):
    """A library that has been fully synchronised, indexed and embedded."""
    ctx.jobs.enqueue("sync_full", dedupe_key="sync")
    run_jobs(ctx)
    failed = ctx.db.all("SELECT type, error FROM jobs WHERE status='failed'")
    assert not failed, [dict(r) for r in failed]
    return ctx
