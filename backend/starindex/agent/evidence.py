"""Evidence: the only material answers may be built from, and how citations are checked.

Every item carries its provenance (repository, path, line range, commit, index time) and an
evidence class: ``observed`` for repository metadata, dependency manifests, activity and
source code we indexed; ``documented`` for what a project says about itself (README, docs).
Answers cite items as ``[E<n>]``; citations that do not resolve are reported, never hidden.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..api.schemas import Evidence, UnsupportedCitation
from ..db import loads
from ..library.repos import find_by_name, maintenance_status
from ..search.service import permalink
from .safety import wrap_untrusted

if TYPE_CHECKING:
    from ..context import AppContext

DOCUMENTED_KINDS = {"readme", "doc", "example"}
CITATION = re.compile(r"\[E(\d+)\]")
_CITE_GROUP = re.compile(r"\[((?:E\d+\s*[,;]\s*)+E\d+)\]")
_REPO_MENTION = re.compile(r"github\.com/([A-Za-z0-9][\w.-]*/[\w.-]+)|`([A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*)`")
_FILE_EXT = re.compile(r"\.(py|js|ts|tsx|jsx|md|json|toml|ya?ml|rs|go|java|txt|html|css|sh|lock|cfg|ini|xml)$", re.IGNORECASE)


def evidence_class_for(kind: str) -> str:
    return "documented" if kind in DOCUMENTED_KINDS else "observed"


class EvidenceSet:
    """Collects evidence with stable refs and a character budget."""

    def __init__(self, ctx: AppContext, *, max_chars: int = 30_000, max_item_chars: int = 1800):
        self.ctx = ctx
        self.db = ctx.db
        self.max_chars = max_chars
        self.max_item_chars = max_item_chars
        self.items: list[Evidence] = []
        self._keys: dict[str, str] = {}
        self.used_chars = 0
        self.dropped = 0

    def __len__(self) -> int:
        return len(self.items)

    @property
    def repo_ids(self) -> list[int]:
        return list(dict.fromkeys(e.repo_id for e in self.items))

    def _add(self, key: str, **fields: Any) -> str | None:
        if key in self._keys:
            return self._keys[key]
        text = fields["text"]
        if len(text) > self.max_item_chars:
            text = text[: self.max_item_chars].rsplit("\n", 1)[0] + "\n…"
            fields["text"] = text
        if self.used_chars + len(text) > self.max_chars:
            self.dropped += 1
            return None
        ref = f"E{len(self.items) + 1}"
        self.items.append(Evidence(ref=ref, **fields))
        self._keys[key] = ref
        self.used_chars += len(text)
        return ref

    # -- sources ----------------------------------------------------------------------------

    def add_chunk(self, chunk_id: int) -> str | None:
        row = self.db.one(
            "SELECT c.id, c.repo_id, c.document_id, c.path, c.kind, c.heading, c.start_line, c.end_line, c.content, c.injection_flag, "
            "d.commit_sha, d.indexed_at, r.full_name, r.html_url, r.default_branch FROM chunks c JOIN documents d ON d.id=c.document_id "
            "JOIN repos r ON r.id=c.repo_id WHERE c.id=?",
            (chunk_id,),
        )
        if row is None:
            return None
        title = row["path"] + (f" — {row['heading']}" if row["heading"] else "")
        return self._add(
            f"chunk:{chunk_id}", evidence_class=evidence_class_for(row["kind"]), kind=row["kind"], repo_id=row["repo_id"],
            repo_full_name=row["full_name"], title=title, text=row["content"], path=row["path"], start_line=row["start_line"],
            end_line=row["end_line"], commit_sha=row["commit_sha"], indexed_at=row["indexed_at"],
            html_url=permalink(row["html_url"], row["commit_sha"] or row["default_branch"], row["path"], row["start_line"], row["end_line"]),
            chunk_id=row["id"], document_id=row["document_id"], injection_flag=bool(row["injection_flag"]),
        )

    def add_metadata(self, repo_id: int) -> str | None:
        r = self.db.one("SELECT * FROM repos WHERE id=?", (repo_id,))
        if r is None:
            return None
        langs = loads(r["languages"], {})
        total = sum(langs.values()) or 1
        lang_text = ", ".join(f"{k} {v * 100 // total}%" for k, v in sorted(langs.items(), key=lambda kv: kv[1], reverse=True)[:5]) or (r["language"] or "unknown")
        cats = ", ".join(
            c["name"] for c in self.db.all(
                "SELECT c.name FROM repo_categories rc JOIN categories c ON c.id=rc.category_id WHERE rc.repo_id=? AND rc.excluded=0", (repo_id,)
            )
        )
        lines = [
            f"Repository: {r['full_name']}",
            f"Description (written by the project): {r['description'] or '(none)'}",
            f"Topics: {', '.join(loads(r['topics'], [])) or '(none)'}",
            f"Languages: {lang_text}",
            f"Stars: {r['stars']}; forks: {r['forks']}; open issues: {r['open_issues']}",
            f"License: {r['license_spdx'] or 'none detected'}",
            f"Created: {(r['created_at'] or '')[:10]}; last push: {(r['pushed_at'] or '')[:10]}; "
            f"maintenance: {maintenance_status(r['pushed_at'], bool(r['is_archived']))}; archived: {'yes' if r['is_archived'] else 'no'}"
            + ("; fork: yes" if r["is_fork"] else ""),
        ]
        if cats:
            lines.append(f"StarIndex categories: {cats}")
        if r["access_state"] != "ok":
            lines.append(f"Availability: {r['access_state']} on GitHub at last check")
        return self._add(
            f"meta:{repo_id}", evidence_class="observed", kind="metadata", repo_id=repo_id, repo_full_name=r["full_name"],
            title="GitHub repository metadata", text="\n".join(lines), html_url=r["html_url"], indexed_at=r["last_seen_at"],
        )

    def add_dependencies(self, repo_id: int, *, limit: int = 40) -> str | None:
        r = self.db.one("SELECT full_name, html_url, indexed_at, indexed_commit_sha FROM repos WHERE id=?", (repo_id,))
        deps = self.db.all(
            "SELECT ecosystem, name, version_spec, scope, manifest_path FROM repo_dependencies WHERE repo_id=? ORDER BY scope='runtime' DESC, name LIMIT ?",
            (repo_id, limit),
        )
        if r is None or not deps:
            return None
        by_manifest: dict[str, list[str]] = {}
        for d in deps:
            by_manifest.setdefault(d["manifest_path"], []).append(
                f"{d['name']}{' ' + d['version_spec'] if d['version_spec'] else ''}" + (f" ({d['scope']})" if d["scope"] != "runtime" else "")
            )
        text = "\n".join(f"{path} [{deps[0]['ecosystem'] if len(by_manifest) == 1 else ''}]: " + ", ".join(names) for path, names in by_manifest.items())
        first = next(iter(by_manifest))
        return self._add(
            f"deps:{repo_id}", evidence_class="observed", kind="dependency", repo_id=repo_id, repo_full_name=r["full_name"],
            title="Declared dependencies", text=text, path=first, commit_sha=r["indexed_commit_sha"], indexed_at=r["indexed_at"],
            html_url=permalink(r["html_url"], r["indexed_commit_sha"], first, None, None),
        )

    def add_file_slice(self, repo_id: int, document_id: int, start: int, end: int) -> str | None:
        row = self.db.one(
            "SELECT d.id, d.path, d.kind, d.content, d.commit_sha, d.indexed_at, r.full_name, r.html_url, r.default_branch "
            "FROM documents d JOIN repos r ON r.id=d.repo_id WHERE d.id=? AND d.repo_id=?",
            (document_id, repo_id),
        )
        if row is None:
            return None
        lines = row["content"].split("\n")
        start = max(1, start)
        end = min(len(lines), max(start, end))
        text = "\n".join(lines[start - 1 : end])
        from ..indexing.chunking import looks_like_injection

        return self._add(
            f"slice:{document_id}:{start}:{end}", evidence_class=evidence_class_for(row["kind"]), kind=row["kind"], repo_id=repo_id,
            repo_full_name=row["full_name"], title=f"{row['path']} (lines {start}–{end})", text=text, path=row["path"], start_line=start,
            end_line=end, commit_sha=row["commit_sha"], indexed_at=row["indexed_at"],
            html_url=permalink(row["html_url"], row["commit_sha"] or row["default_branch"], row["path"], start, end),
            document_id=row["id"], injection_flag=looks_like_injection(text),
        )

    # -- rendering --------------------------------------------------------------------------

    def render(self, refs: list[str] | None = None) -> str:
        """Evidence blocks for a prompt. Content is delimited and neutralised as untrusted data."""
        wanted = set(refs) if refs is not None else None
        blocks = []
        for ev in self.items:
            if wanted is not None and ev.ref not in wanted:
                continue
            attrs = {"ref": ev.ref, "repo": ev.repo_full_name, "class": ev.evidence_class, "source": ev.kind}
            if ev.path:
                attrs["path"] = ev.path
            if ev.start_line:
                attrs["lines"] = f"{ev.start_line}-{ev.end_line}"
            blocks.append(wrap_untrusted(ev.text, label="evidence", attrs=attrs, flagged=ev.injection_flag))
        return "\n\n".join(blocks)

    def index_lines(self) -> str:
        """One line per item, for planning prompts that should not carry full text."""
        return "\n".join(f"[{e.ref}] {e.repo_full_name} · {e.title} ({e.evidence_class})" for e in self.items)


def normalize_citations(text: str) -> str:
    """'[E1, E2]' -> '[E1][E2]' so every citation is individually resolvable."""
    return _CITE_GROUP.sub(lambda m: "".join(f"[{p.strip()}]" for p in re.split(r"[,;]", m.group(1))), text)


def check_answer(ctx: AppContext, text: str, evidence: list[Evidence]) -> tuple[str, list[UnsupportedCitation], list[str]]:
    """Validate an answer against its evidence.

    Returns the answer with unknown citations marked, the list of unsupported items, and warnings.
    """
    text = normalize_citations(text)
    known = {e.ref for e in evidence}
    unsupported: list[UnsupportedCitation] = []
    warnings: list[str] = []

    def fix(m: re.Match[str]) -> str:
        ref = f"E{m.group(1)}"
        if ref in known:
            return m.group(0)
        unsupported.append(UnsupportedCitation(text=f"[{ref}]", reason="Citation does not match any retrieved evidence."))
        return "[unverified]"

    text = CITATION.sub(fix, text)
    cited = set(CITATION.findall(text))
    if evidence and not cited and len(text) > 200:
        warnings.append("The answer contains no citations; treat it as unverified.")

    evidence_repos = {e.repo_full_name.lower() for e in evidence}
    seen: set[str] = set()
    for m in _REPO_MENTION.finditer(text):
        name = (m.group(1) or m.group(2)).rstrip(".,)")
        low = name.lower()
        if low in seen or low in evidence_repos or _FILE_EXT.search(name):
            continue
        seen.add(low)
        if find_by_name(ctx.db, name) is None:
            unsupported.append(
                UnsupportedCitation(text=name, reason="Mentioned repository is not in your starred library; treat this as unverified.")
            )
    if any(e.injection_flag for e in evidence if e.ref[1:] in cited):
        warnings.append("Some cited sources contain instruction-like text; it was treated as quoted data.")
    return text, unsupported, warnings
