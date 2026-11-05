"""Command line entry points: init, serve, backup, reindex, retry, stats."""

from __future__ import annotations

import argparse
import sys

from . import __version__, backup, buildinfo, config, db, jobs, search


def _init(_args: argparse.Namespace) -> int:
    db.init_db()
    print(f"data: {config.DATA_DIR}")
    print(f"database: {config.DB_PATH}")
    print(f"images: {config.IMAGES_DIR}")
    print(f"thumbs: {config.THUMBS_DIR}")
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    host = args.host or config.HOST
    port = args.port or config.PORT
    uvicorn.run(
        "memsave.server.app:app",
        host=host,
        port=port,
        reload=bool(args.reload),
    )
    return 0


def _backup(args: argparse.Namespace) -> int:
    result = backup.run_backup(full=bool(args.full))
    print(f"backup: {result['path']}")
    print(
        f"images: {result['images']}, thumbs: {result['thumbs']}, "
        f"size: {result['size_bytes']} bytes"
    )
    print(f"previous: {result['previous_backup_utc'] or 'never'}")
    print(f"now: {result['backup_utc']}")
    return 0


def _reindex(args: argparse.Namespace) -> int:
    db.init_db()
    with db.session() as conn:
        result = search.reindex(conn, full=bool(args.full))
    print(
        f"reindex: {result['updated']} updated, {result['skipped']} skipped, "
        f"{result['errors']} errors, {result['total']} ready"
    )
    return 0 if result["errors"] == 0 else 1


def _retry(_args: argparse.Namespace) -> int:
    db.init_db()
    count = jobs.retry_failed()
    print(f"queued: {count}")
    return 0


def _stats(_args: argparse.Namespace) -> int:
    db.init_db()
    with db.session() as conn:
        stats = db.stats(conn)
    print(f"images: {stats['images']}")
    print(f"bytes: {stats['bytes']}")
    print(f"tags: {stats['tags']}")
    print(f"embeddings: {stats['embeddings']}")
    for status, count in sorted(stats["by_status"].items()):
        print(f"  {status}: {count}")
    return 0


def _version(_args: argparse.Namespace) -> int:
    info = buildinfo.build_info()
    print(info["version"])
    if info["commit"]:
        print(f"{info['short']} {info['commit_url']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memsave", description="Personal image archive with AI tags")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init", help="create the data directory and database")
    init.set_defaults(func=_init)

    serve = sub.add_parser("serve", help="run the web server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=_serve)

    backup_parser = sub.add_parser("backup", help="create an incremental zip backup")
    backup_parser.add_argument("--full", action="store_true", help="include all images")
    backup_parser.set_defaults(func=_backup)

    reindex = sub.add_parser("reindex", help="rebuild semantic embeddings")
    reindex.add_argument("--full", action="store_true")
    reindex.set_defaults(func=_reindex)

    retry = sub.add_parser("retry-failed", help="re-queue images whose AI step failed")
    retry.set_defaults(func=_retry)

    stats = sub.add_parser("stats", help="print archive statistics")
    stats.set_defaults(func=_stats)

    version = sub.add_parser("version", help="print build metadata")
    version.set_defaults(func=_version)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"memsave: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
