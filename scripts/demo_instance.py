"""Run StarIndex against a local fake GitHub that serves the evaluation corpus.

Used for browser tests and for trying the application without a GitHub account:

    python eval/build_corpus.py          # once, needs internet
    python scripts/demo_instance.py --data-dir ./.demo-data --port 8765

The fake GitHub API listens on --github-port; StarIndex is configured (through environment
variables) to use it instead of api.github.com, with the token "demo-token-for-local-testing-only".
"""

from __future__ import annotations

import argparse
import os
import threading
import time
from pathlib import Path

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=".demo-data")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--github-port", type=int, default=8766)
    parser.add_argument("--limit", type=int, default=0, help="Use only the first N corpus repositories.")
    parser.add_argument("--embedding", default="fastembed", choices=["fastembed", "hashing", "none"])
    parser.add_argument("--connect", action="store_true", help="Pre-configure the GitHub token (skip onboarding).")
    parser.add_argument("--sync", action="store_true", help="Start a full sync immediately.")
    parser.add_argument("--llm-port", type=int, default=8767, help="Port of the scripted fake LLM (OpenAI-compatible). 0 disables it.")
    parser.add_argument("--with-llm", action="store_true", help="Pre-configure the fake LLM as the default model provider.")
    args = parser.parse_args()

    from starindex.testing.corpus import fake_from_corpus

    fake = fake_from_corpus(limit=args.limit or None)
    gh = uvicorn.Server(uvicorn.Config(fake.asgi(), host="127.0.0.1", port=args.github_port, log_level="warning"))
    threading.Thread(target=gh.run, daemon=True).start()

    if args.llm_port:
        from starindex.testing.fake_llm import FakeLLM

        llm = uvicorn.Server(uvicorn.Config(FakeLLM().asgi(), host="127.0.0.1", port=args.llm_port, log_level="warning"))
        threading.Thread(target=llm.run, daemon=True).start()

    data_dir = Path(args.data_dir).resolve()
    os.environ.update(
        STARINDEX_DATA_DIR=str(data_dir),
        STARINDEX_PORT=str(args.port),
        STARINDEX_GITHUB_API_URL=f"http://127.0.0.1:{args.github_port}",
        STARINDEX_GITHUB_RAW_URL=f"http://127.0.0.1:{args.github_port}/raw",
        STARINDEX_DISABLE_KEYRING="1",
    )
    from starindex.config import get_config, reset_config_cache
    from starindex.context import AppContext
    from starindex.logsetup import configure

    reset_config_cache()
    config = get_config()
    config.paths.ensure()
    configure(config.paths.logs_dir, config.log_level)
    ctx = AppContext(config)
    patch: dict = {"embedding": {"backend": args.embedding, **({"model": "hashing-256"} if args.embedding == "hashing" else {})}}
    if args.embedding != "fastembed":
        patch["rerank"] = {"enabled": False}
    if args.connect or args.sync:
        from starindex.secrets import GITHUB_TOKEN

        ctx.secrets.set(GITHUB_TOKEN, "demo-token-for-local-testing-only")
        patch["github"] = {"auth_method": "token"}
    ctx.settings_store.patch(patch)
    if args.sync:
        ctx.jobs.enqueue("sync_full", dedupe_key="sync")
    if args.with_llm and args.llm_port and ctx.providers.get("fake-llm") is None:
        cfg = ctx.providers.create("openai_compat", "Fake LLM", f"http://127.0.0.1:{args.llm_port}/v1", {}, True, None)
        ctx.settings_store.patch({"routing": {"default": {"provider_id": cfg.id, "model": "fake-good"}}})

    from starindex.api.app import create_app

    app = create_app(ctx)
    print(f"StarIndex demo at http://127.0.0.1:{args.port}  (fake GitHub on :{args.github_port}, token: demo-token-for-local-testing-only, data: {data_dir})", flush=True)
    time.sleep(0.3)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
