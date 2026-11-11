"""Deterministic repository comparison. No model is needed for the table itself."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..api.schemas import Comparison, CompareRow, SharedItem
from ..db import loads
from ..library import repos as repo_store
from ..library.repos import maintenance_status
from .evidence import EvidenceSet

if TYPE_CHECKING:
    from ..context import AppContext

LICENSE_FAMILIES = {
    "permissive": {"MIT", "MIT-0", "APACHE-2.0", "BSD-2-CLAUSE", "BSD-3-CLAUSE", "ISC", "UNLICENSE", "0BSD", "CC0-1.0", "ZLIB", "BSL-1.0", "WTFPL", "POSTGRESQL", "CC-BY-4.0"},
    "weak copyleft": {"LGPL-2.1", "LGPL-3.0", "MPL-2.0", "EPL-1.0", "EPL-2.0", "CDDL-1.0", "OFL-1.1", "CC-BY-SA-4.0"},
    "strong copyleft": {"GPL-2.0", "GPL-3.0"},
    "network copyleft": {"AGPL-3.0", "SSPL-1.0", "OSL-3.0", "EUPL-1.2"},
    "source-available (not open source)": {"BUSL-1.1", "ELASTIC-2.0", "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "CC-BY-NC-ND-4.0", "CC-BY-ND-4.0"},
}
FRAMEWORKS = {
    "react": "React", "next": "Next.js", "vue": "Vue", "svelte": "Svelte", "@angular/core": "Angular", "three": "three.js",
    "@react-three/fiber": "react-three-fiber", "express": "Express", "fastify": "Fastify", "hono": "Hono", "tailwindcss": "Tailwind CSS",
    "vite": "Vite", "electron": "Electron", "@modelcontextprotocol/sdk": "MCP SDK", "mcp": "MCP SDK", "fastmcp": "FastMCP",
    "fastapi": "FastAPI", "flask": "Flask", "django": "Django", "pydantic": "Pydantic", "torch": "PyTorch", "tensorflow": "TensorFlow",
    "transformers": "Transformers", "langchain": "LangChain", "langchain-core": "LangChain", "langgraph": "LangGraph",
    "llama-index": "LlamaIndex", "openai": "OpenAI SDK", "anthropic": "Anthropic SDK", "@anthropic-ai/sdk": "Anthropic SDK",
    "qdrant-client": "Qdrant", "chromadb": "Chroma", "sqlalchemy": "SQLAlchemy", "numpy": "NumPy", "pandas": "pandas",
    "tokio": "Tokio", "axum": "Axum", "actix-web": "Actix", "serde": "Serde", "bevy": "Bevy", "wgpu": "wgpu",
    "playwright": "Playwright", "puppeteer": "Puppeteer", "prisma": "Prisma", "drizzle-orm": "Drizzle", "zod": "Zod",
    "gsap": "GSAP", "framer-motion": "Framer Motion", "motion": "Motion", "remotion": "Remotion", "d3": "D3", "onnxruntime": "ONNX Runtime",
    "sentence-transformers": "Sentence Transformers", "litellm": "LiteLLM", "ollama": "Ollama client", "streamlit": "Streamlit", "gradio": "Gradio",
}
_FEATURE_HEADING = re.compile(r"(features|highlights|capabilities|what.?s included|why|key features|overview)", re.IGNORECASE)
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+)$")


def license_family(spdx: str | None) -> str:
    if not spdx:
        return "no license detected"
    key = spdx.upper().removesuffix("-ONLY").removesuffix("-OR-LATER")
    for family, ids in LICENSE_FAMILIES.items():
        if key in ids:
            return family
    return "other / unrecognised"


def license_notes(repos: list[tuple[str, str | None]]) -> list[str]:
    """Plain observations about combining these licenses. Not legal advice."""
    notes: list[str] = []
    fam = {name: license_family(spdx) for name, spdx in repos}
    for name, spdx in repos:
        f = fam[name]
        if f == "no license detected":
            notes.append(f"{name} has no detected license. Without one, default copyright applies and reuse is not granted; check the repository before depending on it.")
        elif f == "network copyleft":
            notes.append(f"{name} is {spdx} (network copyleft): offering a modified version as a network service generally triggers source-sharing obligations.")
        elif f == "strong copyleft":
            notes.append(f"{name} is {spdx} (strong copyleft): distributing a combined work generally requires releasing it under the same license.")
        elif f == "source-available (not open source)":
            notes.append(f"{name} is {spdx}, which restricts some uses (for example commercial or competing use). Read its terms before building on it.")
        elif f == "other / unrecognised":
            notes.append(f"{name} uses {spdx}, which StarIndex does not classify. Review it manually.")
    copyleft = [n for n, f in fam.items() if f in ("strong copyleft", "network copyleft")]
    permissive = [n for n, f in fam.items() if f == "permissive"]
    if copyleft and permissive:
        notes.append(
            f"Combining code from {', '.join(copyleft)} with the permissively licensed {', '.join(permissive)} in one distributed work "
            "would place the combination under the copyleft terms. Using them as separate services or processes is a different situation."
        )
    if not notes:
        notes.append("All compared repositories use permissive licenses; combining them is generally straightforward (keep the notices).")
    notes.append("These are observations from detected SPDX identifiers, not legal advice.")
    return notes


def _features(ctx: AppContext, repo_id: int, evidence: EvidenceSet) -> tuple[str, list[str]]:
    """Feature bullets the project lists in its README (documented claims, not verified)."""
    rows = ctx.db.all(
        "SELECT id, heading, content FROM chunks WHERE repo_id=? AND kind='readme' ORDER BY document_id, ordinal LIMIT 40", (repo_id,)
    )
    for r in rows:
        last = r["heading"].split(" > ")[-1] if r["heading"] else ""
        if _FEATURE_HEADING.search(last):
            bullets = [m.group(1).strip() for line in r["content"].split("\n") if (m := _BULLET.match(line))]
            if bullets:
                ref = evidence.add_chunk(r["id"])
                short = [re.sub(r"\s+", " ", re.sub(r"[*_`]", "", b))[:110] for b in bullets[:5]]
                return "• " + "\n• ".join(short), [ref] if ref else []
    return "—", []


def build_comparison(ctx: AppContext, repo_ids: list[int]) -> Comparison:
    db = ctx.db
    summaries = repo_store.ordered_summaries(db, repo_ids)
    ids = [s.id for s in summaries]
    rows_db = {r["id"]: r for r in db.all(f"SELECT * FROM repos WHERE id IN ({','.join(str(i) for i in ids) or 'NULL'})")}
    evidence = EvidenceSet(ctx, max_chars=40_000, max_item_chars=1500)
    meta_refs = {rid: evidence.add_metadata(rid) for rid in ids}
    dep_refs = {rid: evidence.add_dependencies(rid) for rid in ids}
    deps = {
        rid: db.all("SELECT ecosystem, name, scope, manifest_path FROM repo_dependencies WHERE repo_id=?", (rid,)) for rid in ids
    }
    docs = {rid: {r["path"].lower() for r in db.all("SELECT path FROM documents WHERE repo_id=?", (rid,))} for rid in ids}

    def col(fn) -> list[str]:
        return [str(fn(rows_db[i])) for i in ids]

    def meta(key: str, label: str, group: str, fn) -> CompareRow:
        return CompareRow(key=key, label=label, group=group, values=col(fn), evidence_class="observed",  # type: ignore[arg-type]
                          evidence_refs=[[meta_refs[i]] if meta_refs[i] else [] for i in ids])

    def langs(r) -> str:
        data = loads(r["languages"], {})
        total = sum(data.values()) or 1
        top = sorted(data.items(), key=lambda kv: kv[1], reverse=True)[:4]
        return ", ".join(f"{k} {v * 100 // total}%" for k, v in top) or (r["language"] or "—")

    rows: list[CompareRow] = [
        meta("description", "Description", "identity", lambda r: r["description"] or "—"),
        meta("topics", "Topics", "identity", lambda r: ", ".join(loads(r["topics"], [])[:10]) or "—"),
        meta("homepage", "Homepage", "identity", lambda r: r["homepage"] or "—"),
    ]
    cat_values = [
        ", ".join(c["name"] for c in db.all(
            "SELECT c.name FROM repo_categories rc JOIN categories c ON c.id=rc.category_id WHERE rc.repo_id=? AND rc.excluded=0", (i,))) or "—"
        for i in ids
    ]
    rows.append(CompareRow(key="categories", label="Categories (StarIndex)", group="capabilities", values=cat_values, evidence_class="inferred"))
    feats = [_features(ctx, i, evidence) for i in ids]
    rows.append(CompareRow(key="features", label="Features listed in README", group="capabilities", values=[f[0] for f in feats],
                           evidence_class="documented", evidence_refs=[f[1] for f in feats]))

    rows.append(meta("languages", "Languages", "architecture", langs))
    fw_values = []
    for i in ids:
        names = list(dict.fromkeys(FRAMEWORKS[d["name"].lower()] for d in deps[i] if d["name"].lower() in FRAMEWORKS and d["scope"] in ("runtime", "peer")))
        fw_values.append(", ".join(names[:8]) or ("—" if deps[i] else "no manifest indexed"))
    rows.append(CompareRow(key="frameworks", label="Frameworks (from manifests)", group="architecture", values=fw_values,
                           evidence_class="observed", evidence_refs=[[dep_refs[i]] if dep_refs[i] else [] for i in ids]))
    deploy = []
    for i in ids:
        hints = []
        paths = docs[i]
        if any(p.endswith("dockerfile") for p in paths):
            hints.append("Dockerfile")
        if any("compose" in p for p in paths):
            hints.append("Docker Compose")
        eco = sorted({d["ecosystem"] for d in deps[i] if d["ecosystem"] != "docker"})
        if eco:
            hints.append("packages: " + ", ".join(eco))
        deploy.append("; ".join(hints) or "—")
    rows.append(CompareRow(key="deployment", label="Packaging and deployment files", group="architecture", values=deploy, evidence_class="observed"))
    rows.append(meta("size", "Repository size", "architecture", lambda r: f"{r['size_kb'] / 1024:.1f} MB"))

    rows += [
        meta("stars", "Stars", "maturity", lambda r: f"{r['stars']:,}"),
        meta("forks", "Forks", "maturity", lambda r: f"{r['forks']:,}"),
        meta("open_issues", "Open issues", "maturity", lambda r: f"{r['open_issues']:,}"),
        meta("created", "Created", "maturity", lambda r: (r["created_at"] or "—")[:10]),
        meta("pushed", "Last push", "maturity", lambda r: (r["pushed_at"] or "—")[:10]),
        meta("maintenance", "Maintenance", "maturity", lambda r: maintenance_status(r["pushed_at"], bool(r["is_archived"]))),
        meta("indexed", "Indexed in StarIndex", "maturity", lambda r: f"{r['indexed_level'] or 'not indexed'}" + (f" @ {r['indexed_commit_sha'][:7]}" if r["indexed_commit_sha"] else "")),
    ]
    dep_counts = [f"{len({d['name'] for d in deps[i] if d['scope'] in ('runtime', 'peer')})} runtime" + (f", {len({d['name'] for d in deps[i] if d['scope'] == 'dev'})} dev" if deps[i] else "") if deps[i] else "no manifest indexed" for i in ids]
    rows.append(CompareRow(key="dep_count", label="Declared dependencies", group="dependencies", values=dep_counts, evidence_class="observed",
                           evidence_refs=[[dep_refs[i]] if dep_refs[i] else [] for i in ids]))
    top_deps = [", ".join(list(dict.fromkeys(d["name"] for d in deps[i] if d["scope"] in ("runtime", "peer")))[:10]) or "—" for i in ids]
    rows.append(CompareRow(key="dep_top", label="Runtime dependencies", group="dependencies", values=top_deps, evidence_class="observed",
                           evidence_refs=[[dep_refs[i]] if dep_refs[i] else [] for i in ids]))
    rows += [
        meta("license", "License", "licensing", lambda r: r["license_spdx"] or "none detected"),
        meta("license_family", "License family", "licensing", lambda r: license_family(r["license_spdx"])),
    ]

    topic_map: dict[str, list[int]] = {}
    for i in ids:
        for t in loads(rows_db[i]["topics"], []):
            topic_map.setdefault(t.lower(), []).append(i)
    dep_map: dict[str, list[int]] = {}
    for i in ids:
        for name in {f"{d['ecosystem']}:{d['name']}" for d in deps[i] if d["scope"] in ("runtime", "peer")}:
            dep_map.setdefault(name, []).append(i)
    return Comparison(
        repos=summaries,
        rows=rows,
        shared_topics=[SharedItem(name=t, repo_ids=r) for t, r in sorted(topic_map.items(), key=lambda kv: -len(kv[1])) if len(r) > 1][:20],
        shared_dependencies=[SharedItem(name=n, repo_ids=r) for n, r in sorted(dep_map.items(), key=lambda kv: -len(kv[1])) if len(r) > 1][:30],
        license_notes=license_notes([(rows_db[i]["full_name"], rows_db[i]["license_spdx"]) for i in ids]),
        evidence=evidence.items,
    )


def comparison_as_text(comparison: Comparison) -> str:
    """Compact table for prompts and Markdown export."""
    names = [r.full_name for r in comparison.repos]
    lines = ["| | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for row in comparison.rows:
        cells = [v.replace("\n", "; ").replace("|", "/")[:220] for v in row.values]
        lines.append(f"| {row.label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)
