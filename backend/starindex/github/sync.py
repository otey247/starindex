"""Discovery of starred repositories (separate from content indexing).

Full sync walks every page of the starred list, checkpointing after each page inside the
same transaction that stores the page, so an interrupted run resumes without duplicates or
gaps. Only a *completed* walk may conclude that something was unstarred, and each candidate
is verified individually before it is marked. Incremental sync uses a conditional request
on the newest page and stops as soon as it reaches known territory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..db import dumps, loads, utcnow
from ..jobs.handle import JobHandle, inline_job
from ..jobs.queue import RetryLater
from ..library import repos as repo_store
from .client import GitHubClient, GitHubError, RateLimited, StarPage

if TYPE_CHECKING:
    from ..context import AppContext

log = logging.getLogger(__name__)

# If a completed walk would unstar more than this share of the library, something is wrong
# (truncated listing, wrong account). Refuse and report instead of hiding half the library.
MASS_UNSTAR_GUARD = 0.5
MASS_UNSTAR_MIN = 25


@dataclass
class SyncOutcome:
    run_id: int
    kind: str
    status: str = "succeeded"
    pages: int = 0
    seen: int = 0
    added: int = 0
    updated: int = 0
    renamed: int = 0
    unstarred: int = 0
    restarred: int = 0
    not_modified: bool = False
    escalate_full: bool = False
    new_repo_ids: list[int] = field(default_factory=list)
    changed_repo_ids: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "kind": self.kind, "status": self.status, "pages": self.pages, "seen": self.seen,
            "added": self.added, "updated": self.updated, "renamed": self.renamed, "unstarred": self.unstarred,
            "restarred": self.restarred, "not_modified": self.not_modified, "warnings": self.warnings,
        }


class StarSync:
    def __init__(self, ctx: AppContext, client: GitHubClient | None = None):
        self.ctx = ctx
        self.db = ctx.db
        self.client = client or ctx.github

    # -- run bookkeeping ------------------------------------------------------------------

    def _open_run(self, kind: str, job: JobHandle) -> tuple[int, dict[str, Any]]:
        """Resume the run recorded in the job checkpoint, or start a new one."""
        run_id = job.state.get("run_id")
        if run_id:
            row = self.db.one("SELECT status, checkpoint FROM sync_runs WHERE id=? AND kind=?", (run_id, kind))
            if row and row["status"] in ("running", "interrupted"):
                with self.db.tx() as c:
                    c.execute("UPDATE sync_runs SET status='running' WHERE id=?", (run_id,))
                return run_id, loads(row["checkpoint"], {})
        with self.db.tx() as c:
            # Any older run still marked running belongs to a process that died.
            c.execute("UPDATE sync_runs SET status='interrupted', finished_at=? WHERE status='running'", (utcnow(),))
            run_id = int(
                c.execute("INSERT INTO sync_runs(kind, status, started_at) VALUES (?, 'running', ?)", (kind, utcnow())).lastrowid
            )
        job.state["run_id"] = run_id
        job.save_state()
        return run_id, {}

    def _finish_run(self, out: SyncOutcome, error: str | None = None) -> None:
        with self.db.tx() as c:
            c.execute(
                "UPDATE sync_runs SET status=?, finished_at=?, pages_fetched=?, repos_seen=?, added=?, updated=?, renamed=?, "
                "unstarred=?, restarred=?, not_modified=?, error=? WHERE id=?",
                (out.status, utcnow(), out.pages, out.seen, out.added, out.updated, out.renamed, out.unstarred,
                 out.restarred, int(out.not_modified), error, out.run_id),
            )

    def _store_page(self, page: StarPage, out: SyncOutcome, *, next_url: str | None) -> tuple[int, int]:
        """Persist one page and the resume checkpoint atomically. Returns (known, total) counts."""
        level = self.ctx.settings.indexing.default_level
        known = 0
        touched: list[int] = []
        with self.db.tx() as c:
            for item in page.items:
                repo = item["repo"]
                if not isinstance(repo, dict) or "id" not in repo:
                    continue
                before = c.execute("SELECT star_state, starred_at FROM repos WHERE id=?", (int(repo["id"]),)).fetchone()
                res = repo_store.upsert_from_github(
                    c, repo, starred_at=item.get("starred_at"), run_id=out.run_id, default_index_level=level
                )
                out.seen += 1
                if res.outcome == "added":
                    out.added += 1
                    out.new_repo_ids.append(res.repo_id)
                    touched.append(res.repo_id)
                else:
                    if (
                        before is not None
                        and before["star_state"] == "starred"
                        and (item.get("starred_at") is None or before["starred_at"] == item.get("starred_at"))
                    ):
                        known += 1
                    if res.outcome == "updated":
                        out.updated += 1
                        touched.append(res.repo_id)
                    if res.pushed_changed:
                        out.changed_repo_ids.append(res.repo_id)
                if res.renamed:
                    out.renamed += 1
                if res.restarred:
                    out.restarred += 1
                    touched.append(res.repo_id)
            out.pages += 1
            checkpoint = {
                "next_url": next_url, "pages": out.pages, "seen": out.seen, "added": out.added, "updated": out.updated,
                "renamed": out.renamed, "restarred": out.restarred, "new_repo_ids": out.new_repo_ids[-5000:],
                "changed_repo_ids": out.changed_repo_ids[-5000:],
            }
            c.execute(
                "UPDATE sync_runs SET checkpoint=?, pages_fetched=?, repos_seen=?, added=?, updated=?, renamed=?, restarred=? WHERE id=?",
                (dumps(checkpoint), out.pages, out.seen, out.added, out.updated, out.renamed, out.restarred, out.run_id),
            )
            if touched:
                from ..search.lexical import refresh_repo_docs

                refresh_repo_docs(c, touched)
        return known, len(page.items)

    # -- full -----------------------------------------------------------------------------

    def run_full(self, job: JobHandle | None = None) -> SyncOutcome:
        job = job or inline_job()
        run_id, checkpoint = self._open_run("full", job)
        out = SyncOutcome(run_id=run_id, kind="full")
        # Restore counters when resuming.
        for key in ("pages", "seen", "added", "updated", "renamed", "restarred"):
            setattr(out, key, int(checkpoint.get(key, 0)))
        out.new_repo_ids = list(checkpoint.get("new_repo_ids", []))
        out.changed_repo_ids = list(checkpoint.get("changed_repo_ids", []))
        try:
            expected = self.client.starred_count()
            with self.db.tx() as c:
                c.execute("UPDATE sync_runs SET total_expected=? WHERE id=?", (expected, run_id))

            if checkpoint.get("walk_complete"):
                url = None
            else:
                url = checkpoint.get("next_url") or self.client.first_star_page_url()
            while url:
                job.progress(out.seen, expected or 0, f"Fetching starred repositories (page {out.pages + 1})")
                page = self.client.star_page(url)
                self._store_page(page, out, next_url=page.next_url)
                url = page.next_url
                if not page.items:
                    break
            with self.db.tx() as c:
                c.execute(
                    "UPDATE sync_runs SET checkpoint=json_set(checkpoint, '$.walk_complete', json('true'), '$.next_url', NULL) WHERE id=?",
                    (run_id,),
                )

            job.progress(out.seen, expected or out.seen, "Reconciling unstarred and renamed repositories")
            self._reconcile(out, job)
            self._apply_retention(out)
            # The newest-page ETag is refreshed by the next incremental run.
            self.client.forget_etag(self.client.first_star_page_url())
        except RateLimited as exc:
            # Keep the run open; the job is re-queued and resumes from the checkpoint.
            with self.db.tx() as c:
                c.execute("UPDATE sync_runs SET status='interrupted' WHERE id=?", (run_id,))
            raise RetryLater(exc.wait_s + 2, exc.message) from exc
        except GitHubError as exc:
            if exc.kind in ("network", "server"):
                with self.db.tx() as c:
                    c.execute("UPDATE sync_runs SET status='interrupted', error=? WHERE id=?", (exc.message, run_id))
                raise
            out.status = "failed"
            self._finish_run(out, exc.message)
            raise
        self._finish_run(out)
        job.state.pop("run_id", None)
        return out

    def _reconcile(self, out: SyncOutcome, job: JobHandle) -> None:
        """Decide what happened to starred repositories the completed walk did not return."""
        candidates = self.db.all(
            "SELECT id, full_name FROM repos WHERE star_state='starred' AND access_state='ok' "
            "AND (last_seen_run IS NULL OR last_seen_run<>?)",
            (out.run_id,),
        )
        if not candidates:
            return
        total = self.db.scalar("SELECT count(*) FROM repos WHERE star_state='starred'", default=0)
        if len(candidates) >= MASS_UNSTAR_MIN and len(candidates) > total * MASS_UNSTAR_GUARD:
            msg = (
                f"{len(candidates)} of {total} repositories were missing from GitHub's starred list. "
                "Nothing was marked as unstarred; check the connected account and run a full sync again."
            )
            out.warnings.append(msg)
            log.warning(msg)
            return
        now = utcnow()
        for i, row in enumerate(candidates):
            job.progress(message=f"Verifying {row['full_name']} ({i + 1}/{len(candidates)})")
            try:
                fresh = self.client.repo_by_id(row["id"])
            except GitHubError as exc:
                if exc.kind in ("not_found", "blocked", "forbidden"):
                    state = "blocked" if exc.kind == "blocked" else "not_found"
                    with self.db.tx() as c:
                        c.execute(
                            "UPDATE repos SET access_state=?, access_checked_at=?, last_seen_run=? WHERE id=?",
                            (state, now, out.run_id, row["id"]),
                        )
                    continue
                raise
            # The repository still exists (possibly renamed or transferred): refresh it by id.
            still_starred = self.client.is_starred(fresh.get("full_name") or row["full_name"])
            with self.db.tx() as c:
                res = repo_store.upsert_from_github(c, fresh, starred_at=None, run_id=None, mark_starred=False)
                if res.renamed:
                    out.renamed += 1
                if still_starred:
                    # Missed by the walk (the list shifted while paging); it is still starred.
                    c.execute("UPDATE repos SET last_seen_run=?, star_state='starred' WHERE id=?", (out.run_id, row["id"]))
                else:
                    c.execute(
                        "UPDATE repos SET star_state='unstarred', unstarred_at=?, last_seen_run=? WHERE id=?",
                        (now, out.run_id, row["id"]),
                    )
                    out.unstarred += 1
                from ..search.lexical import refresh_repo_docs

                refresh_repo_docs(c, [row["id"]])

    def _apply_retention(self, out: SyncOutcome) -> None:
        policy = self.ctx.settings.sync.unstarred_retention
        if policy == "keep":
            return
        removed: list[int] = []
        for row in self.db.all("SELECT id FROM repos WHERE star_state='unstarred'"):
            if policy == "keep_annotated" and repo_store.has_personal_data(self.db, row["id"]):
                continue
            removed.append(row["id"])
        if not removed:
            return
        from ..indexing.pipeline import purge_repo

        for repo_id in removed:
            purge_repo(self.ctx, repo_id)
        out.warnings.append(f"Removed {len(removed)} unstarred repositories (retention: {policy}).")

    # -- incremental ----------------------------------------------------------------------

    def run_incremental(self, job: JobHandle | None = None) -> SyncOutcome:
        job = job or inline_job()
        run_id, _ = self._open_run("incremental", job)
        out = SyncOutcome(run_id=run_id, kind="incremental")
        try:
            first_url = self.client.first_star_page_url()
            job.progress(0, 0, "Checking for newly starred repositories")
            page = self.client.star_page(first_url, conditional=True)
            if page.not_modified:
                out.not_modified = True
            else:
                while True:
                    known, total = self._store_page(page, out, next_url=page.next_url)
                    job.progress(out.seen, 0, f"Fetched {out.seen} recently starred repositories")
                    # Stop once a whole page is already known: everything older is unchanged in order.
                    if not page.next_url or total == 0 or known == total:
                        break
                    page = self.client.star_page(page.next_url)
            # Unstarring an old repository does not change the newest page, so compare totals.
            expected = self.client.starred_count()
            local = self.db.scalar("SELECT count(*) FROM repos WHERE star_state='starred' AND access_state='ok'", default=0)
            with self.db.tx() as c:
                c.execute("UPDATE sync_runs SET total_expected=? WHERE id=?", (expected, run_id))
            if expected is not None and expected != local:
                out.escalate_full = True
                out.warnings.append(
                    f"GitHub reports {expected} starred repositories but {local} are stored; scheduling a full reconciliation."
                )
        except RateLimited as exc:
            with self.db.tx() as c:
                c.execute("UPDATE sync_runs SET status='interrupted' WHERE id=?", (run_id,))
            raise RetryLater(exc.wait_s + 2, exc.message) from exc
        except GitHubError as exc:
            out.status = "failed"
            self._finish_run(out, exc.message)
            raise
        self._finish_run(out)
        job.state.pop("run_id", None)
        return out

    # -- targeted refresh -------------------------------------------------------------------

    def refresh_repo(self, repo_id: int) -> bool:
        """Re-read one repository's metadata by id (handles renames and transfers)."""
        try:
            fresh = self.client.repo_by_id(repo_id)
        except GitHubError as exc:
            if exc.kind in ("not_found", "blocked", "forbidden"):
                with self.db.tx() as c:
                    c.execute(
                        "UPDATE repos SET access_state=?, access_checked_at=? WHERE id=?",
                        ("blocked" if exc.kind == "blocked" else "not_found", utcnow(), repo_id),
                    )
                return False
            raise
        with self.db.tx() as c:
            repo_store.upsert_from_github(c, fresh, starred_at=None, run_id=None, mark_starred=False)
            from ..search.lexical import refresh_repo_docs

            refresh_repo_docs(c, [repo_id])
        return True
