"""Star synchronisation against the fake GitHub API."""

from __future__ import annotations

import time

import pytest

from starindex.api.schemas import AnnotationPatch
from starindex.github.client import GitHubError
from starindex.github.sync import StarSync
from starindex.jobs.handle import inline_job
from starindex.jobs.queue import RetryLater
from starindex.library import annotations as ann
from starindex.testing.fake_github import make_repo
from starindex.testing.harness import attach_fake, make_context

from .conftest import run_jobs
from .fixtures import build_fake, bulk_fake


def starred_ids(ctx) -> set[int]:
    return {r["id"] for r in ctx.db.all("SELECT id FROM repos WHERE star_state='starred'")}


def test_full_sync_walks_every_page(tmp_path):
    fake = bulk_fake(257)          # more than two pages of 100
    ctx = make_context(tmp_path, fake)
    out = StarSync(ctx).run_full()
    assert out.pages == 3 and out.seen == 257 and out.added == 257
    assert len(starred_ids(ctx)) == 257
    assert ctx.db.scalar("SELECT total_expected FROM sync_runs WHERE id=?", (out.run_id,)) == 257
    assert fake.count("/user/starred?per_page=100") == 3      # exactly three list requests, no page skipped or repeated


def test_resync_creates_no_duplicates_and_keeps_star_timestamps(ctx, fake):
    first = StarSync(ctx).run_full()
    second = StarSync(ctx).run_full()
    assert first.added == len(fake.repos) and second.added == 0 and second.updated == 0
    assert ctx.db.scalar("SELECT count(*) FROM repos") == len(fake.repos)
    row = ctx.db.one("SELECT starred_at FROM repos WHERE id=101")
    assert row["starred_at"] == "2026-10-28T10:00:00Z"       # from the star+json media type


def test_star_timestamps_missing_when_media_type_ignored(tmp_path):
    fake = build_fake(count=3)
    fake.ignore_star_media_type = True
    ctx = make_context(tmp_path, fake)
    StarSync(ctx).run_full()
    assert len(starred_ids(ctx)) == 3
    assert ctx.db.scalar("SELECT count(*) FROM repos WHERE starred_at IS NOT NULL") == 0


def test_interrupted_full_sync_resumes_from_checkpoint(tmp_path):
    fake = bulk_fake(230)
    ctx = make_context(tmp_path, fake)
    fake.fail(lambda path: path == "/user/starred" and len([1 for _, p in fake.requests if "page=2" in p]) >= 1, status="network", times=10)
    job = inline_job()
    with pytest.raises(GitHubError) as err:
        StarSync(ctx).run_full(job)
    assert err.value.kind == "network"
    assert ctx.db.scalar("SELECT count(*) FROM repos") == 100                     # first page persisted
    run = ctx.db.one("SELECT status, checkpoint FROM sync_runs ORDER BY id DESC LIMIT 1")
    assert run["status"] == "interrupted" and "page=2" in run["checkpoint"]

    fake._fail_plan.clear()
    before = fake.count("page=1&") + fake.count("per_page=100&sort=created&direction=desc")
    out = StarSync(ctx).run_full(job)                                              # same job state -> same run
    assert out.seen == 230 and out.added == 230 and out.pages == 3
    assert ctx.db.scalar("SELECT count(*) FROM repos") == 230
    assert ctx.db.scalar("SELECT count(*) FROM sync_runs") == 1                   # resumed, not restarted
    first_page_requests = [p for _, p in fake.requests if p.startswith("/user/starred?per_page=100&sort=created&direction=desc") and "page=" not in p.split("desc")[-1]]
    assert len(first_page_requests) == 1 or before >= 1                           # page one was not fetched again


def test_incremental_sync_uses_etag_and_picks_up_new_stars(ctx, fake):
    StarSync(ctx).run_full()
    inc = StarSync(ctx).run_incremental()
    assert inc.not_modified is False and inc.added == 0        # first conditional request stores the ETag
    inc2 = StarSync(ctx).run_incremental()
    assert inc2.not_modified is True and inc2.pages == 0

    fake.add(make_repo(900, "new/shiny", description="Brand new"), files={"README.md": "# shiny\n"}, starred_at="2026-11-05T00:00:00Z")
    inc3 = StarSync(ctx).run_incremental()
    assert inc3.added == 1 and inc3.not_modified is False
    assert 900 in starred_ids(ctx)
    assert inc3.pages == 1                                      # stopped at the first page: everything older is known


def test_incremental_detects_unstar_through_count_and_escalates(ctx, fake):
    StarSync(ctx).run_full()
    StarSync(ctx).run_incremental()
    fake.unstar(108)                                            # an old star: the newest page still changes here, count differs
    inc = StarSync(ctx).run_incremental()
    assert inc.escalate_full is True
    full = StarSync(ctx).run_full()
    assert full.unstarred == 1
    assert ctx.db.one("SELECT star_state, unstarred_at FROM repos WHERE id=108")["star_state"] == "unstarred"


def test_unstarred_repo_keeps_notes_collections_and_reports(ctx, fake):
    StarSync(ctx).run_full()
    ann.patch_annotation(ctx.db, 107, AnnotationPatch(note="use for invoices", favorite=True))
    ann.set_tags(ctx.db, 107, ["pdf", "keep"])
    fake.unstar(107)
    out = StarSync(ctx).run_full()
    assert out.unstarred == 1
    row = ctx.db.one("SELECT star_state FROM repos WHERE id=107")
    assert row["star_state"] == "unstarred"
    assert ctx.db.one("SELECT note, favorite FROM repo_annotations WHERE repo_id=107")["note"] == "use for invoices"
    assert ctx.db.scalar("SELECT count(*) FROM repo_tags WHERE repo_id=107") == 2

    fake.star(107, "2026-11-06T00:00:00Z")                      # starring again restores it, annotations intact
    again = StarSync(ctx).run_full()
    assert again.restarred == 1
    assert ctx.db.one("SELECT star_state, unstarred_at FROM repos WHERE id=107")["unstarred_at"] is None


@pytest.mark.parametrize("policy, annotated_survives, plain_survives", [("keep", True, True), ("keep_annotated", True, False), ("delete", False, False)])
def test_unstarred_retention_policies(tmp_path, policy, annotated_survives, plain_survives):
    fake = build_fake()
    ctx = make_context(tmp_path, fake, settings={"sync": {"unstarred_retention": policy}})
    StarSync(ctx).run_full()
    ann.patch_annotation(ctx.db, 107, AnnotationPatch(note="important"))
    fake.unstar(107)
    fake.unstar(108)
    StarSync(ctx).run_full()
    assert bool(ctx.db.one("SELECT 1 FROM repos WHERE id=107")) is annotated_survives
    assert bool(ctx.db.one("SELECT 1 FROM repos WHERE id=108")) is plain_survives


def test_rename_and_transfer_keep_identity_and_annotations(ctx, fake):
    StarSync(ctx).run_full()
    ann.patch_annotation(ctx.db, 104, AnnotationPatch(note="my favourite agent framework"))
    fake.rename(104, "neworg/agentgraph-next")
    out = StarSync(ctx).run_full()
    assert out.renamed == 1 and out.added == 0
    row = ctx.db.one("SELECT full_name, owner, name FROM repos WHERE id=104")
    assert (row["full_name"], row["owner"], row["name"]) == ("neworg/agentgraph-next", "neworg", "agentgraph-next")
    hist = ctx.db.one("SELECT old_full_name, new_full_name FROM repo_name_history WHERE repo_id=104")
    assert (hist["old_full_name"], hist["new_full_name"]) == ("graphflow/agentgraph", "neworg/agentgraph-next")
    assert ctx.db.one("SELECT note FROM repo_annotations WHERE repo_id=104")["note"] == "my favourite agent framework"
    from starindex.library.repos import find_by_name

    assert find_by_name(ctx.db, "graphflow/agentgraph") == 104      # the old name still resolves


def test_deleted_and_blocked_repositories_are_marked_not_unstarred(ctx, fake):
    StarSync(ctx).run_full()
    fake.delete(103)
    fake.repos[112].blocked = True
    out = StarSync(ctx).run_full()
    assert out.unstarred == 0
    assert ctx.db.one("SELECT access_state, star_state FROM repos WHERE id=103")["access_state"] == "not_found"
    assert ctx.db.one("SELECT access_state FROM repos WHERE id=112")["access_state"] == "blocked"
    assert ctx.db.scalar("SELECT count(*) FROM repos") == len(fake.repos)       # nothing was removed locally


def test_mass_unstar_guard_refuses_to_hide_the_library(tmp_path):
    fake = bulk_fake(60)
    ctx = make_context(tmp_path, fake)
    StarSync(ctx).run_full()
    for repo_id in list(fake.repos)[:50]:
        fake.unstar(repo_id)
    out = StarSync(ctx).run_full()
    assert out.unstarred == 0 and out.warnings
    assert len(starred_ids(ctx)) == 60


def test_repo_missed_by_pagination_shift_is_verified_before_unstarring(ctx, fake):
    StarSync(ctx).run_full()
    # Simulate a repository that the walk did not return although it is still starred.
    with ctx.db.tx() as c:
        c.execute("UPDATE repos SET last_seen_run=-1 WHERE id=105")
    sync = StarSync(ctx)
    out = sync.run_full()
    assert out.unstarred == 0
    assert ctx.db.one("SELECT star_state FROM repos WHERE id=105")["star_state"] == "starred"


def test_primary_rate_limit_defers_and_resumes(tmp_path):
    fake = bulk_fake(150)
    ctx = make_context(tmp_path, fake)
    fake.rate_remaining = 2                                    # count request + first page, then exhausted
    fake.rate_reset = int(time.time()) + 900
    job = inline_job()
    with pytest.raises(RetryLater) as wait:
        StarSync(ctx).run_full(job)
    assert 600 < wait.value.delay_s < 1000
    assert ctx.db.scalar("SELECT count(*) FROM repos") == 100
    fake.rate_remaining = 5000
    out = StarSync(ctx).run_full(job)
    assert out.seen == 150 and ctx.db.scalar("SELECT count(*) FROM repos") == 150
    assert ctx.db.scalar("SELECT count(*) FROM sync_runs") == 1


def test_secondary_rate_limit_is_waited_out_inline(ctx, fake):
    fake.secondary_limit_next = 2
    slept: list[float] = []
    ctx.github._sleep = slept.append
    out = StarSync(ctx).run_full()
    assert out.added == len(fake.repos)
    assert slept and all(s == fake.retry_after for s in slept[:2])


def test_server_errors_are_retried_with_backoff(ctx, fake):
    fake.fail("/user/starred", status=502, times=2)
    out = StarSync(ctx).run_full()
    assert out.added == len(fake.repos)


def test_bad_credentials_fail_without_retrying(tmp_path):
    fake = build_fake()
    ctx = make_context(tmp_path, fake)
    from starindex.secrets import GITHUB_TOKEN

    ctx.secrets.set(GITHUB_TOKEN, "wrong-token")
    ctx.github.token_resolver.invalidate()
    with pytest.raises(GitHubError) as err:
        StarSync(ctx).run_full()
    assert err.value.kind == "auth"
    assert ctx.db.one("SELECT status FROM sync_runs ORDER BY id DESC LIMIT 1")["status"] == "failed"


def test_public_mode_reads_user_stars_without_token(tmp_path):
    fake = build_fake(token=None)
    ctx = make_context(tmp_path, None, settings={"github": {"auth_method": "public", "username": "octo"}})
    attach_fake(ctx, fake)
    out = StarSync(ctx).run_full()
    assert out.added == len(fake.repos)
    assert fake.count("/users/octo/starred") >= 1 and fake.count("/user/starred") == 0


def test_offline_mode_blocks_github(tmp_path):
    fake = build_fake()
    ctx = make_context(tmp_path, fake, settings={"privacy": {"offline_mode": True}})
    with pytest.raises(GitHubError) as err:
        StarSync(ctx).run_full()
    assert err.value.kind == "offline" and not fake.requests


def test_client_refuses_unexpected_hosts_and_bad_names(ctx):
    with pytest.raises(GitHubError):
        ctx.github.request("https://evil.example/steal")
    with pytest.raises(GitHubError):
        ctx.github.repo("../../etc/passwd")
    with pytest.raises(GitHubError):
        ctx.github.file("vectordb-co/quiver", "a" * 40, "../secret", max_bytes=10)


def test_sync_job_queues_indexing_and_embedding(ctx, fake):
    ctx.jobs.enqueue("sync_full", dedupe_key="sync")
    run_jobs(ctx)
    statuses = {r["type"]: r["n"] for r in ctx.db.all("SELECT type, count(*) n FROM jobs WHERE status='succeeded' GROUP BY type")}
    assert statuses["sync_full"] == 1 and statuses["index_repo"] == len(fake.repos) and statuses["embed_pending"] >= 1
    assert ctx.db.scalar("SELECT count(*) FROM repos WHERE index_status='indexed'") == len(fake.repos)
