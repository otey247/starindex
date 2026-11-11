"""Download the evaluation corpus: public metadata and READMEs of the repositories in corpus_repos.txt.

Uses only public, unauthenticated GitHub endpoints (repository search for metadata, raw file
host for READMEs and one manifest). Output goes to eval/corpus_cache/ (git-ignored): the
READMEs belong to their projects and are not redistributed with StarIndex.

    python eval/build_corpus.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

HERE = Path(__file__).parent
CACHE = HERE / "corpus_cache"
README_NAMES = ["README.md", "readme.md", "README.rst", "README", "Readme.md", "README.MD", "README.txt"]
MANIFESTS = ["package.json", "pyproject.toml", "Cargo.toml", "go.mod", "requirements.txt"]
MAX_README = 60_000
MAX_MANIFEST = 20_000


def load_names() -> list[str]:
    names = []
    for line in (HERE / "corpus_repos.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.append(line)
    return list(dict.fromkeys(names))


def fetch_metadata(client: httpx.Client, names: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    batch: list[str] = []
    batches: list[list[str]] = []
    for name in names:
        if len(" ".join(f"repo:{n}" for n in [*batch, name])) > 230 or len(batch) >= 6:
            batches.append(batch)
            batch = []
        batch.append(name)
    if batch:
        batches.append(batch)
    for i, group in enumerate(batches):
        q = " ".join(f"repo:{n}" for n in group) + " fork:true"
        for _attempt in range(5):
            resp = client.get("https://api.github.com/search/repositories", params={"q": q, "per_page": 20},
                              headers={"Accept": "application/vnd.github+json"})
            if resp.status_code == 200:
                for item in resp.json().get("items", []):
                    found[item["full_name"].lower()] = item
                break
            if resp.status_code in (403, 429):
                wait = int(resp.headers.get("retry-after") or 0) or max(5, int(resp.headers.get("x-ratelimit-reset", "0")) - int(time.time()) + 1)
                time.sleep(min(wait, 70))
                continue
            if resp.status_code == 422:      # one repository in the batch no longer exists: query them one by one
                for single in group:
                    r = client.get("https://api.github.com/search/repositories", params={"q": f"repo:{single} fork:true"})
                    if r.status_code == 200:
                        for item in r.json().get("items", []):
                            found[item["full_name"].lower()] = item
                    time.sleep(2.2)
                break
            time.sleep(3)
        print(f"metadata batch {i + 1}/{len(batches)}: {len(found)} repositories", flush=True)
        time.sleep(2.2)          # unauthenticated search: 10 requests/minute; authenticated: 30
    return found


def fetch_files(client: httpx.Client, item: dict) -> dict[str, str]:
    full = item["full_name"]
    branch = item.get("default_branch") or "HEAD"
    files: dict[str, str] = {}
    for name in README_NAMES:
        try:
            r = client.get(f"https://raw.githubusercontent.com/{full}/{branch}/{name}")
        except httpx.HTTPError:
            continue
        if r.status_code == 200 and r.text.strip():
            files[name] = r.text[:MAX_README]
            break
    for name in MANIFESTS:
        try:
            r = client.get(f"https://raw.githubusercontent.com/{full}/{branch}/{name}")
        except httpx.HTTPError:
            continue
        if r.status_code == 200 and r.text.strip():
            files[name] = r.text[:MAX_MANIFEST]
            break
    return files


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    names = load_names()
    if args.limit:
        names = names[: args.limit]
    CACHE.mkdir(exist_ok=True)
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "starindex-eval-corpus"}) as client:
        meta = fetch_metadata(client, names)
        missing = [n for n in names if n.lower() not in meta]
        items = list(meta.values())
        with ThreadPoolExecutor(max_workers=8) as pool:
            files = list(pool.map(lambda it: fetch_files(client, it), items))
    keep = (
        "id node_id name full_name private html_url description fork homepage size stargazers_count watchers_count forks_count "
        "open_issues_count language license topics archived disabled is_template default_branch created_at updated_at pushed_at"
    ).split()
    corpus = []
    for item, f in zip(items, files, strict=True):
        repo = {k: item.get(k) for k in keep}
        repo["owner"] = {"login": item["owner"]["login"], "type": item["owner"].get("type"), "avatar_url": item["owner"].get("avatar_url")}
        repo["clone_url"] = item.get("clone_url")
        if repo.get("license"):
            repo["license"] = {"spdx_id": repo["license"].get("spdx_id"), "name": repo["license"].get("name")}
        corpus.append({"repo": repo, "files": f})
    corpus.sort(key=lambda c: c["repo"]["full_name"].lower())
    (CACHE / "corpus.json").write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
    with_readme = sum(1 for c in corpus if any(k.lower().startswith("readme") for k in c["files"]))
    print(f"saved {len(corpus)} repositories ({with_readme} with README) to {CACHE / 'corpus.json'}")
    if missing:
        print("not found (renamed, removed or private):", ", ".join(missing))


if __name__ == "__main__":
    main()
