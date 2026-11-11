"""Build an isolated AppContext wired to a FakeGitHub (tests, evaluation, demos)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..config import reset_config_cache
from ..context import AppContext
from ..github.client import build_client
from .fake_github import FakeGitHub

FAKE_API = "http://github.test"


def make_context(data_dir: Path, fake: FakeGitHub | None = None, *, embedding: str = "none", settings: dict[str, Any] | None = None) -> AppContext:
    os.environ["STARINDEX_DATA_DIR"] = str(data_dir)
    os.environ["STARINDEX_DISABLE_KEYRING"] = "1"
    os.environ["STARINDEX_GITHUB_API_URL"] = FAKE_API
    os.environ["STARINDEX_GITHUB_RAW_URL"] = f"{FAKE_API}/raw"
    reset_config_cache()
    ctx = AppContext()
    patch: dict[str, Any] = {
        "embedding": {"backend": embedding, **({"model": "hashing-256"} if embedding == "hashing" else {})},
        "rerank": {"enabled": False},
        "github": {"auth_method": "token"},
    }
    for key, value in (settings or {}).items():
        patch[key] = {**patch.get(key, {}), **value} if isinstance(value, dict) else value
    ctx.settings_store.patch(patch)
    if fake is not None:
        attach_fake(ctx, fake)
    return ctx


def attach_fake(ctx: AppContext, fake: FakeGitHub) -> None:
    from ..secrets import GITHUB_TOKEN

    if fake.token:
        ctx.secrets.set(GITHUB_TOKEN, fake.token)
    client = build_client(ctx, transport=fake.transport())
    client._sleep = lambda s: None          # never really sleep in tests
    ctx.__dict__["github"] = client
