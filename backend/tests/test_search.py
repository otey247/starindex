"""Lexical, vector and hybrid retrieval over the hand-written fixture library (no LLM anywhere)."""

from __future__ import annotations

import pytest

from starindex.api.schemas import CollectionCreate, RepoFilters, SearchRequest
from starindex.indexing.pipeline import index_repo
from starindex.library import annotations as ann
from starindex.providers.base import ProviderError
from starindex.search.query import fts_expression, parse_query

from .conftest import run_jobs


def names(res) -> list[str]:
    return [r.repo.full_name for r in res.results]


def search(ctx, query, **kw):
    return ctx.search.search(SearchRequest(query=query, record=False, **kw))


def test_query_parser_operators_and_expression():
    q, f = parse_query('agents "multi-agent" lang:python stars:>1000 -archived is:favorite pushed:>2026-01-01 topic:mcp license:mit owner:acme in:readme')
    assert q.terms == ["agents"] and q.phrases == ["multi-agent"] and q.excluded == ["archived"]
    assert f.languages == ["python"] and f.min_stars == 1001 and f.favorite is True and f.pushed_after == "2026-01-01"
    assert f.topics == ["mcp"] and f.licenses == ["mit"] and f.owners == ["acme"]
    expr = fts_expression(q, columns=("readme",))
    assert '"agents"' in expr and '"multi agent"' in expr and 'NOT "archived"' in expr and expr.startswith("{readme}")
    q2, f2 = parse_query("stars:100..5k size:<2000 is:archived unknown:thing OR other")
    assert (f2.min_stars, f2.max_stars, f2.max_size_kb, f2.archived) == (100, 5000, 1999, True)
    assert q2.any_mode and "unknown" in q2.terms and "thing" in q2.terms
    q3, _ = parse_query("find all my starred repositories related to vector search")
    assert q3.terms == ["vector", "search"]                     # filler words carry no signal


def test_exact_name_search_ranks_the_repository_first(synced):
    for mode in ("keyword", "hybrid", "semantic"):
        res = search(synced, "quiver", mode=mode)
        assert names(res)[0] == "vectordb-co/quiver", mode       # the upstream outranks its low-star fork
        assert res.results[0].signals.name_match
    assert names(search(synced, "react-trifiber", mode="keyword"))[0] == "trifiber/react-trifiber"
    assert names(search(synced, "crewlabs/crewkit", mode="keyword"))[0] == "crewlabs/crewkit"
    assert names(search(synced, "trifiber", mode="keyword"))[0] == "trifiber/react-trifiber"       # part of a hyphenated name


def test_phrase_search_requires_adjacent_words(synced):
    assert set(names(search(synced, '"developer portal"', mode="keyword"))) == {"apigate/apigate", "oldtools/legacy-gateway"}
    assert names(search(synced, '"portal developer"', mode="keyword")) == []


def test_typo_tolerance_corrects_unknown_terms(synced):
    res = search(synced, "vectr databse", mode="keyword")
    assert {c.original: c.corrected for c in res.parsed.corrections} == {"vectr": "vector", "databse": "database"}
    assert names(res)[0] in {"vectordb-co/quiver", "embedlabs/chromatic", "someone/quiver"}
    assert search(synced, "orchestraton", mode="keyword").parsed.corrections[0].corrected == "orchestration"
    # A correctly spelled word that only occurs in README text is not "corrected".
    assert search(synced, "transitions", mode="keyword").parsed.corrections == []
    synced.settings_store.patch({"search": {"typo_tolerance": False}})
    assert names(search(synced, "vectr databse", mode="keyword")) == []


def test_filters_and_operators(synced):
    assert set(names(search(synced, "lang:rust"))) == {"vectordb-co/quiver", "someone/quiver"}
    assert names(search(synced, "database lang:python", mode="keyword")) == ["embedlabs/chromatic"]
    assert set(names(search(synced, "", filters=RepoFilters(licenses=["none"])))) == {"motionworks/framecut"}
    assert set(names(search(synced, "is:archived"))) == {"oldtools/legacy-gateway"}
    assert "oldtools/legacy-gateway" not in names(search(synced, "api gateway", filters=RepoFilters(archived=False)))
    assert set(names(search(synced, "stars:>20000"))) == {"vectordb-co/quiver", "crewlabs/crewkit", "trifiber/react-trifiber"}
    assert set(names(search(synced, "topic:mcp-server"))) == {"ctxproto/mcp-files"}
    assert set(names(search(synced, "", filters=RepoFilters(maintenance=["stale", "archived"])))) == {"oldtools/legacy-gateway"}
    assert names(search(synced, "", filters=RepoFilters(fork=True))) == ["someone/quiver"]
    without = names(search(synced, "agents -crew", mode="keyword"))
    assert without[0] == "graphflow/agentgraph" and "crewlabs/crewkit" not in without
    assert "crewlabs/crewkit" in names(search(synced, "agents", mode="keyword"))
    res = search(synced, "pdf", mode="hybrid", filters=RepoFilters(licenses=["MIT"]))
    assert names(res) and all(r.repo.license_spdx == "MIT" for r in res.results)       # filter holds on the semantic side too


def test_lexical_retrieval_finds_readme_and_doc_passages(synced):
    res = search(synced, "payload filtering", mode="keyword")
    assert names(res)[0] == "vectordb-co/quiver"
    match = res.results[0].matches[0]
    assert match.path == "README.md" and "«" in match.snippet and match.start_line >= 1
    docs = search(synced, "create a collection", mode="keyword", scope="docs")
    assert docs.results[0].matches[0].path == "docs/quickstart.md"
    assert search(synced, "search_collection", mode="keyword", scope="code").results == []     # no code at documentation level


def test_vector_retrieval_and_hybrid_fusion(synced):
    sem = search(synced, "similarity search engine", mode="semantic")
    assert sem.mode == "semantic" and sem.embedding_model == "hashing:hashing-256"
    assert names(sem)[0] in {"vectordb-co/quiver", "pgext/pgvec", "someone/quiver"}
    hyb = search(synced, "vector similarity search", mode="hybrid", explain=True)
    assert hyb.mode == "hybrid"
    top = hyb.results[0]
    assert top.signals.lexical_rank is not None and top.signals.semantic_rank is not None       # found by both retrievers
    assert top.signals.fused_score and 1.0 <= top.signals.prior <= 1.07                          # priors stay bounded
    assert set(names(hyb)[:4]) >= {"vectordb-co/quiver", "pgext/pgvec"}


def test_one_result_per_repository_and_passage_diversity(synced):
    res = search(synced, "vector search database", mode="hybrid")
    assert len(names(res)) == len(set(names(res)))
    assert all(len(r.matches) <= 3 for r in res.results)
    passages = search(synced, "search", mode="hybrid", scope="docs", limit=50)
    per_repo: dict[int, int] = {}
    for r in passages.results:
        per_repo[r.repo.id] = per_repo.get(r.repo.id, 0) + 1
    assert max(per_repo.values()) <= 3


def test_popularity_cannot_override_relevance(synced):
    with synced.db.tx() as c:
        c.execute("UPDATE repos SET stars=5000000 WHERE id=109")                # an unrelated repository becomes hugely popular
    assert "trifiber/react-trifiber" not in names(search(synced, "extract tables from pdf", mode="hybrid"))[:2]
    assert names(search(synced, "extract tables from pdf", mode="hybrid"))[0] == "pdfworks/pdfsmith"


def test_citations_are_stable_across_reindexing(synced, fake):
    def citation():
        m = search(synced, "create a collection", mode="keyword", scope="docs").results[0].matches[0]
        uid = synced.db.scalar("SELECT uid FROM chunks WHERE id=?", (m.chunk_id,))
        return m, uid

    before, uid_before = citation()
    assert before.commit_sha == fake.repos[101].sha
    assert before.html_url == f"https://github.com/vectordb-co/quiver/blob/{before.commit_sha}/docs/quickstart.md?plain=1#L{before.start_line}-L{before.end_line}"
    old = fake.repos[101].files["docs/quickstart.md"].decode()
    fake.push(101, {"docs/quickstart.md": old.replace("# Quick start\n\n", "# Quick start\n\nThis guide was revised for the second release.\n\nRead the release notes first.\n\n", 1)})
    index_repo(synced, 101)
    run_jobs(synced)
    after, uid_after = citation()
    assert after.commit_sha == fake.repos[101].sha != before.commit_sha        # citation points at the new commit
    assert after.heading == before.heading and uid_after == uid_before          # same passage, same identity
    assert after.start_line > before.start_line                                 # line range follows the text


def test_search_works_with_no_llm_configured(synced):
    assert synced.router.active("answer").configured is False
    with pytest.raises(ProviderError):
        synced.router.resolve("answer")
    for mode in ("keyword", "semantic", "hybrid"):
        assert names(search(synced, "api management", mode=mode))


def test_degrades_to_keyword_when_embeddings_are_unavailable(synced):
    synced.settings_store.patch({"embedding": {"backend": "none"}})
    res = search(synced, "api management", mode="hybrid")
    assert res.mode == "keyword" and res.requested_mode == "hybrid" and res.notices and names(res)

    synced.settings_store.patch({"embedding": {"backend": "fastembed", "model": "does-not/exist"}, "privacy": {"offline_mode": True}})
    synced.reset_service("embeddings")
    with synced.db.tx() as c:
        c.execute("UPDATE repos SET embedded_model_key='fastembed:does-not/exist'")       # pretend an index exists for it
    res = search(synced, "api management", mode="semantic")
    assert res.mode == "keyword" and "unavailable" in res.notices[0].lower() and names(res)


def test_semantic_not_ready_before_embedding(ctx, fake):
    from starindex.github.sync import StarSync

    StarSync(ctx).run_full()                                    # metadata only: nothing indexed or embedded yet
    with ctx.db.tx() as c:
        from starindex.search.lexical import refresh_repo_docs

        refresh_repo_docs(c, [r["id"] for r in ctx.db.all("SELECT id FROM repos")])
    res = search(ctx, "vector database", mode="hybrid")
    assert res.mode == "keyword" and "still being built" in res.notices[0]
    assert "vectordb-co/quiver" in names(res)[:3]              # usable as soon as metadata exists


def test_facets_history_saved_and_smart_collections(synced):
    res = synced.search.search(SearchRequest(query="database"))
    assert {f.value for f in res.facets.languages} >= {"Rust", "Python"}
    assert synced.db.scalar("SELECT count(*) FROM search_history") == 1
    synced.search.search(SearchRequest(query="database"))
    assert synced.db.scalar("SELECT count(*) FROM search_history") == 1          # repeated query is not duplicated

    cid = ann.create_collection(synced.db, CollectionCreate(name="Rust things", kind="smart", smart_query=SearchRequest(query="lang:rust")))
    assert set(names(search(synced, "", filters=RepoFilters(collections=[cid])))) == {"vectordb-co/quiver", "someone/quiver"}
    assert set(names(search(synced, 'collection:"Rust things" similarity', mode="keyword"))) <= {"vectordb-co/quiver", "someone/quiver"}
    manual = ann.create_collection(synced.db, CollectionCreate(name="Docs", repo_ids=[107, 108]))
    assert set(names(search(synced, "", filters=RepoFilters(collections=[manual])))) == {"pdfworks/pdfsmith", "docmill/docmill"}


def test_notes_and_tags_are_searchable_and_unstarred_hidden(synced, fake):
    from starindex.api.schemas import AnnotationPatch
    from starindex.github.sync import StarSync

    ann.patch_annotation(synced.db, 109, AnnotationPatch(note="candidate for the house builder prototype"))
    assert names(search(synced, "house builder prototype", mode="keyword"))[0] == "trifiber/react-trifiber"
    fake.unstar(109)
    StarSync(synced).run_full()
    assert "trifiber/react-trifiber" not in names(search(synced, "house builder prototype", mode="hybrid"))
    assert names(search(synced, "house builder prototype is:unstarred", mode="keyword")) == ["trifiber/react-trifiber"]


def test_suggestions_and_related(synced):
    from starindex.library import insights

    sug = synced.search.suggest("qui")
    assert sug[0].kind == "repo" and sug[0].text == "vectordb-co/quiver"
    assert any(s.text == "lang:Rust" for s in synced.search.suggest("lang:ru"))
    rel = insights.related(synced, 101, limit=5)
    by_name = {r.repo.full_name: r for r in rel}
    assert "someone/quiver" in by_name and "embedlabs/chromatic" in by_name
    assert any(x.kind == "topics" for x in by_name["embedlabs/chromatic"].reasons)
    deps = insights.dependencies(synced, 104)
    assert next(d for d in deps if d.name == "pydantic").shared_with >= 2
