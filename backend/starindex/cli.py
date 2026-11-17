"""Command line entry point: `starindex <command>`."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import webbrowser


def _ctx():
    from .config import get_config
    from .context import AppContext
    from .logsetup import configure

    config = get_config()
    config.paths.ensure()
    configure(config.paths.logs_dir, config.log_level)
    return AppContext(config)


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .config import get_config, reset_config_cache
    from .logsetup import configure

    if args.host:
        os.environ["STARINDEX_HOST"] = args.host
    if args.port:
        os.environ["STARINDEX_PORT"] = str(args.port)
    if args.no_worker:
        os.environ["STARINDEX_WORKER"] = "external"
    reset_config_cache()
    config = get_config()
    config.paths.ensure()
    configure(config.paths.logs_dir, config.log_level)
    from .api.app import create_app

    app = create_app()
    url = f"http://{config.host}:{config.port}"
    print(f"StarIndex is running at {url}  (data: {config.paths.data_dir})")
    if args.open:
        import threading

        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=config.host, port=config.port, log_config=None, access_log=False)
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    from .jobs.worker import Worker

    ctx = _ctx()
    if not ctx.config.qdrant_url:
        print(
            "The embedded vector index can be opened by one process only. Run the worker inside the server (default),\n"
            "or set STARINDEX_QDRANT_URL to a Qdrant server to run a separate worker.", file=sys.stderr,
        )
        return 2
    Worker(ctx, mode="external").run_forever()
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    from .jobs.worker import Worker

    ctx = _ctx()
    ctx.jobs.enqueue("sync_full" if args.full else "sync_incremental", dedupe_key="sync")
    worker = Worker(ctx, mode="embedded")
    started = time.time()
    while True:
        ran = worker.drain()
        waiting = ctx.db.scalar("SELECT count(*) FROM jobs WHERE status='queued'", default=0)
        if not waiting:
            break
        if not ran:
            time.sleep(2)       # deferred jobs (rate limits, backoff)
        if args.max_minutes and time.time() - started > args.max_minutes * 60:
            print("Stopped at the time limit; remaining work resumes on the next run.")
            break
    failed = ctx.db.scalar("SELECT count(*) FROM jobs WHERE status='failed'", default=0)
    print(json.dumps({
        "starred": ctx.db.scalar("SELECT count(*) FROM repos WHERE star_state='starred'", default=0),
        "indexed": ctx.db.scalar("SELECT count(*) FROM repos WHERE index_status='indexed'", default=0),
        "chunks": ctx.db.scalar("SELECT count(*) FROM chunks", default=0),
        "failed_jobs": failed,
    }))
    ctx.close()
    return 1 if failed else 0


def cmd_search(args: argparse.Namespace) -> int:
    from .api.schemas import SearchRequest

    ctx = _ctx()
    res = ctx.search.search(SearchRequest(query=" ".join(args.query), mode=args.mode, limit=args.limit, record=False))
    for note in res.notices:
        print(f"note: {note}")
    for i, r in enumerate(res.results, start=1):
        print(f"{i:2}. {r.repo.full_name}  ★{r.repo.stars}  {r.repo.language or ''}\n    {r.repo.description or ''}")
        for m in r.matches[:1]:
            print(f"    {m.path}:{m.start_line}  {m.snippet[:160]}")
    print(f"{res.total} results in {res.took_ms} ms ({res.mode})")
    ctx.close()
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .api.routes.system import diagnostics

    ctx = _ctx()
    worst = 0
    for check in diagnostics(ctx).checks:
        if check.name == "Background worker" and check.status == "fail":
            # From the command line this only means the application is not running right now.
            check.status, check.detail, check.hint = "info", "StarIndex is not running at the moment.", "Start it with scripts/start.ps1 (or `starindex serve`)."
        mark = {"ok": "ok  ", "info": "info", "warn": "WARN", "fail": "FAIL"}[check.status]
        print(f"[{mark}] {check.name}: {check.detail}" + (f"\n       → {check.hint}" if check.hint else ""))
        if check.status == "fail" and check.name not in ("Background worker", "GitHub"):
            worst = 1
    ctx.close()
    return worst


def cmd_backup(args: argparse.Namespace) -> int:
    from .storage import create_backup, list_backups, restore_backup

    ctx = _ctx()
    if args.action == "create":
        print(create_backup(ctx))
    elif args.action == "list":
        for b in list_backups(ctx):
            print(f"{b['name']}  {b['size_bytes'] / 1e6:.1f} MB  {b['created_at']}")
    elif args.action == "restore":
        if not args.name:
            print("Give the backup name to restore (see `starindex backup list`).", file=sys.stderr)
            return 2
        restore_backup(ctx, args.name)
        print("Restored. Start StarIndex to rebuild the vector index.")
    ctx.close()
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .storage import export_library

    ctx = _ctx()
    name, _, body = export_library(ctx, args.format)
    target = args.output or name
    with open(target, "w", encoding="utf-8", newline="") as fh:
        fh.write(body)
    print(target)
    ctx.close()
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    """MCP server over stdio. stdout carries protocol messages only, so logging stays in the log file."""
    import logging

    from .config import get_config
    from .mcp_server import run_stdio

    config = get_config()
    config.paths.ensure()
    logging.basicConfig(filename=str(config.paths.logs_dir / "mcp.log"), level=logging.WARNING)
    base = args.url or f"http://127.0.0.1:{config.port}"
    run_stdio(base, config.access_token)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .config import get_config
    from .verify import run

    config = get_config()
    base = args.url or f"http://127.0.0.1:{config.port}"
    report = Path(args.report) if args.report else config.paths.data_dir / "verification-report.md"
    return run(base, config.access_token, sync=args.sync, research=args.research, query=args.query,
               question=args.question, report_path=report, wait_minutes=args.wait_minutes)


def cmd_paths(args: argparse.Namespace) -> int:
    from .config import get_config

    p = get_config().paths
    print(json.dumps({"data_dir": str(p.data_dir), "database": str(p.db_path), "vectors": str(p.vector_dir), "models": str(p.models_dir),
                      "backups": str(p.backups_dir), "logs": str(p.logs_dir)}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="starindex", description="Local-first AI search for your starred GitHub repositories.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="Run the application (API, worker and web interface).")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--open", action="store_true", help="Open the browser when ready.")
    p.add_argument("--no-worker", action="store_true", help="Do not run the background worker in this process.")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("worker", help="Run a standalone background worker (needs a Qdrant server).")
    p.set_defaults(fn=cmd_worker)

    p = sub.add_parser("sync", help="Synchronise stars and index content without starting the server.")
    p.add_argument("--full", action="store_true")
    p.add_argument("--max-minutes", type=float, default=0)
    p.set_defaults(fn=cmd_sync)

    p = sub.add_parser("search", help="Search from the terminal.")
    p.add_argument("query", nargs="+")
    p.add_argument("--mode", choices=["keyword", "semantic", "hybrid"], default="hybrid")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("doctor", help="Check dependencies and configuration.")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("backup", help="Create, list or restore database backups.")
    p.add_argument("action", choices=["create", "list", "restore"])
    p.add_argument("name", nargs="?")
    p.set_defaults(fn=cmd_backup)

    p = sub.add_parser("export", help="Export the library with annotations.")
    p.add_argument("--format", choices=["json", "csv"], default="json")
    p.add_argument("--output")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("mcp", help="Run as an MCP server over stdio (for Claude Code, Claude Desktop and other agents).")
    p.add_argument("--url", help="Address of the running StarIndex application (default: http://127.0.0.1:<port>).")
    p.set_defaults(fn=cmd_mcp)

    p = sub.add_parser("verify", help="Check a running installation end to end and write a report.")
    p.add_argument("--url", help="Address of the running application (default: http://127.0.0.1:<port>).")
    p.add_argument("--sync", action="store_true", help="Start a full synchronisation and wait for the starred list.")
    p.add_argument("--research", action="store_true", help="Also run a research request with the default local model (slow).")
    p.add_argument("--query", default="vector database", help="Query used for the search checks.")
    p.add_argument("--question", default="Which of my starred repositories help with semantic search? Name them and say why.")
    p.add_argument("--wait-minutes", type=float, default=30, help="Total time --sync waits for discovery, indexing and embeddings.")
    p.add_argument("--report", help="Where to write the Markdown report (default: the data directory).")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("paths", help="Show where StarIndex keeps its data.")
    p.set_defaults(fn=cmd_paths)

    args = parser.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
