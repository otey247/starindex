"""Load the downloaded evaluation corpus into a FakeGitHub."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .fake_github import FakeGitHub

DEFAULT_CORPUS = Path(__file__).resolve().parents[3] / "eval" / "corpus_cache" / "corpus.json"


def load_corpus(path: Path | None = None) -> list[dict]:
    path = path or DEFAULT_CORPUS
    if not path.is_file():
        raise FileNotFoundError(f"{path} is missing. Run `python eval/build_corpus.py` first (needs internet).")
    return json.loads(path.read_text(encoding="utf-8"))


def fake_from_corpus(path: Path | None = None, *, login: str = "octo", token: str | None = "demo-token", limit: int | None = None) -> FakeGitHub:
    """A FakeGitHub whose starred list is the corpus, starred one per day in a stable order."""
    fake = FakeGitHub(login=login, token=token)
    corpus = load_corpus(path)
    if limit:
        corpus = corpus[:limit]
    base = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    # Stable pseudo-random order so "recently starred" is a mix of topics.
    order = sorted(range(len(corpus)), key=lambda i: (corpus[i]["repo"]["id"] * 2654435761) % 1_000_003)
    for rank, i in enumerate(order):
        entry = corpus[i]
        starred = (base - timedelta(days=rank, minutes=rank * 7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        fake.add(entry["repo"], files=entry["files"], starred_at=starred)
    return fake
