"""Verification must not accept stale syncs or report unfinished work as passing."""

from copy import deepcopy

import pytest

from starindex.verify import FAIL, MANUAL, PASS, SKIP, Verifier


def sync_status(**index):
    return {
        "last_full": {"id": 1, "status": "succeeded", "total_expected": 2, "repos_seen": 2, "pages_fetched": 1},
        "index": {"total": 2, "indexed": 2, "pending": 0, "queued": 0, "indexing": 0,
                  "failed": 0, "skipped": 0, "documents": 2, "chunks": 4,
                  "chunks_embedded": 4, "repos_embedded": 2, **index},
        "queue": {"failed": 0, "by_type": {}},
    }


@pytest.fixture
def verifier(monkeypatch):
    v = Verifier("http://starindex.test")
    monkeypatch.setattr("starindex.verify.time.sleep", lambda _: None)
    yield v
    v.http.close()


def setup_sync(monkeypatch, verifier, *, jobs, statuses):
    jobs, statuses = iter(jobs), iter(statuses)
    seen = []

    def get(path):
        seen.append(path)
        if path == "/api/github/auth":
            return {"method": "gh_cli", "ok": True, "detail": "Connected"}
        if path == "/api/jobs/42":
            return {"id": 42, **next(jobs)}
        assert path == "/api/sync/status"
        return deepcopy(next(statuses))

    monkeypatch.setattr(verifier, "get", get)
    monkeypatch.setattr(verifier, "post", lambda path, body: {"id": 42, "type": "sync_full"})
    return seen


def test_sync_waits_for_requested_job_and_embedding_queue(verifier, monkeypatch):
    embedding = sync_status(chunks_embedded=2)
    embedding["queue"]["by_type"] = {"embed_pending": {"running": 1}}
    seen = setup_sync(monkeypatch, verifier,
                      jobs=[{"status": "queued"}, {"status": "running"}, {"status": "succeeded"}],
                      statuses=[sync_status(pending=1, indexed=1), embedding, sync_status(), sync_status()])
    verifier.github(True, 1)
    assert seen.count("/api/jobs/42") == 3
    assert seen.count("/api/sync/status") == 4
    assert all(c.status == PASS for c in verifier.checks)


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_failed_requested_sync_cannot_reuse_old_success(verifier, monkeypatch, status):
    seen = setup_sync(monkeypatch, verifier, jobs=[{"status": status, "error": "stopped"}],
                      statuses=[sync_status()])
    verifier.github(True, 1)
    assert verifier.exit_code() == 1
    assert "/api/sync/status" not in seen
    assert not any(c.name == "Full synchronisation" and c.status == PASS for c in verifier.checks)


def test_sync_timeout_cannot_reuse_old_success(verifier, monkeypatch):
    setup_sync(monkeypatch, verifier, jobs=[{"status": "queued"}], statuses=[sync_status()])
    verifier.github(True, 0)
    assert verifier.exit_code() == 1
    assert "Timed out" in verifier.checks[-1].detail


def test_indexing_timeout_is_failure(verifier, monkeypatch):
    unfinished = sync_status(pending=1, indexed=1, chunks_embedded=2)
    setup_sync(monkeypatch, verifier, jobs=[{"status": "succeeded"}], statuses=[unfinished, unfinished])
    verifier.github(True, 0)
    assert verifier.exit_code() == 1
    assert any(c.name == "Documentation indexing" and c.status == FAIL for c in verifier.checks)


def test_unembedded_repository_is_not_a_pass(verifier, monkeypatch):
    setup_sync(monkeypatch, verifier, jobs=[], statuses=[sync_status(repos_embedded=1)])
    verifier.github(False, 1)
    assert next(c for c in verifier.checks if c.name == "Embeddings").status == SKIP
    assert verifier.exit_code() == 2


@pytest.mark.parametrize("status,code,outcome", [(PASS, 0, "PASSED"), (FAIL, 1, "FAILED"),
                                                (SKIP, 2, "INCOMPLETE"), (MANUAL, 2, "INCOMPLETE")])
def test_report_and_exit_code_distinguish_incomplete(verifier, status, code, outcome):
    verifier.add("test", "test", status)
    assert verifier.exit_code() == code
    assert f"**{outcome}:" in verifier.report()
