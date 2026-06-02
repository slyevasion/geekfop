from __future__ import annotations

import argparse
from pathlib import Path

from feed_app.db import (
    delete_user_source,
    get_user_source,
    initialize_database,
    list_user_sources,
    upsert_user_source,
)
from feed_app.defaults import CATEGORIES, DEFAULT_DB_PATH, DEFAULT_RUN_DIR
from feed_app.ingest import ingest_sources
from feed_app.models import SOURCE_TYPES, FeedConfigError, FeedSource
from feed_app.sources import parse_bool
from feed_app.validator import validate_all_sources_to_db, validate_source_to_db


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local multi-source feed app.")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init-db", help="Create SQLite DB and seed defaults.")
    init_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)

    init_sources_parser = subparsers.add_parser(
        "init-sources", help="Alias for init-db kept for phase-1 continuity."
    )
    init_sources_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)

    list_parser = subparsers.add_parser("list-sources", help="List configured sources.")
    add_common_source_args(list_parser)
    list_parser.add_argument("--category", choices=CATEGORIES)

    add_parser = subparsers.add_parser("add-source", help="Add or replace a source.")
    add_common_source_args(add_parser)
    add_parser.add_argument("--id", required=True)
    add_parser.add_argument("--name", required=True)
    add_parser.add_argument("--category", required=True, choices=CATEGORIES)
    add_parser.add_argument("--feed-url")
    add_parser.add_argument("--homepage-url")
    add_parser.add_argument("--source-type", default="rss", choices=SOURCE_TYPES)
    add_parser.add_argument("--disabled", action="store_true")
    add_parser.add_argument("--pull-frequency-minutes", type=int, default=360)
    add_parser.add_argument("--notes")

    set_parser = subparsers.add_parser("set-source", help="Update one source.")
    add_common_source_args(set_parser)
    set_parser.add_argument("id")
    set_parser.add_argument("--name")
    set_parser.add_argument("--category", choices=CATEGORIES)
    set_parser.add_argument("--feed-url")
    set_parser.add_argument("--homepage-url")
    set_parser.add_argument("--source-type", choices=SOURCE_TYPES)
    set_parser.add_argument("--enabled", choices=("true", "false", "yes", "no", "on", "off"))
    set_parser.add_argument("--pull-frequency-minutes", type=int)
    set_parser.add_argument("--notes")

    remove_parser = subparsers.add_parser("remove-source", help="Remove one source.")
    add_common_source_args(remove_parser)
    remove_parser.add_argument("id")

    validate_parser = subparsers.add_parser("validate-source", help="Validate one source feed URL.")
    add_common_source_args(validate_parser)
    validate_parser.add_argument("id")
    validate_parser.add_argument("--timeout", type=float, default=12)

    validate_all_parser = subparsers.add_parser(
        "validate-sources", help="Validate all configured source feed URLs."
    )
    add_common_source_args(validate_all_parser)
    validate_all_parser.add_argument("--timeout", type=float, default=12)
    validate_all_parser.add_argument("--workers", type=int, default=8)

    sync_parser = subparsers.add_parser("sync", help="Fetch enabled RSS/Atom sources.")
    add_common_source_args(sync_parser)
    sync_parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    sync_parser.add_argument("--category", choices=CATEGORIES)
    sync_parser.add_argument("--source-id", action="append", default=[])
    sync_parser.add_argument("--timeout", type=float, default=20)
    sync_parser.add_argument("--limit-per-source", type=int, default=40)

    serve_parser = subparsers.add_parser("serve", help="Start local web UI.")
    add_common_source_args(serve_parser)
    serve_parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)

    return parser


def add_common_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    try:
        if args.command in {"init-db", "init-sources"}:
            return run_init_sources(args)
        if args.command == "list-sources":
            return run_list_sources(args)
        if args.command == "add-source":
            return run_add_source(args)
        if args.command == "set-source":
            return run_set_source(args)
        if args.command == "remove-source":
            return run_remove_source(args)
        if args.command == "validate-source":
            return run_validate_source(args)
        if args.command == "validate-sources":
            return run_validate_sources(args)
        if args.command == "sync":
            return run_sync(args)
        if args.command == "serve":
            return run_serve(args)
    except FeedConfigError as exc:
        parser.error(str(exc))
    return 2


def run_init_sources(args: argparse.Namespace) -> int:
    initialize_database(args.db)
    print(f"Ready {args.db}")
    return 0


def run_list_sources(args: argparse.Namespace) -> int:
    sources = list_user_sources(args.db)
    for source in sources:
        if args.category and source.category != args.category:
            continue
        status = "on" if source.enabled else "off"
        feed_url = source.feed_url or "no feed URL"
        print(
            f"[{status}/{source.status}] {source.category:13} "
            f"{source.id:32} {source.name} -> {feed_url}"
        )
    return 0


def run_add_source(args: argparse.Namespace) -> int:
    source = FeedSource(
        id=args.id,
        name=args.name,
        category=args.category,
        homepage_url=args.homepage_url,
        feed_url=args.feed_url,
        source_type=args.source_type,
        enabled=not args.disabled,
        pull_frequency_minutes=args.pull_frequency_minutes,
        notes=args.notes,
    )
    saved = upsert_user_source(args.db, source)
    print(f"Saved source {saved.id}")
    return 0


def run_set_source(args: argparse.Namespace) -> int:
    source = get_user_source(args.db, args.id)
    enabled = parse_bool(args.enabled) if args.enabled is not None else None
    saved = upsert_user_source(
        args.db,
        FeedSource(
            id=source.id,
            name=source.name if args.name is None else args.name,
            category=source.category if args.category is None else args.category,
            homepage_url=source.homepage_url if args.homepage_url is None else args.homepage_url,
            feed_url=source.feed_url if args.feed_url is None else args.feed_url,
            source_type=source.source_type if args.source_type is None else args.source_type,
            enabled=source.enabled if enabled is None else enabled,
            pull_frequency_minutes=(
                source.pull_frequency_minutes
                if args.pull_frequency_minutes is None
                else args.pull_frequency_minutes
            ),
            global_source_id=source.global_source_id,
            last_fetched_at=source.last_fetched_at,
            fetch_status=source.fetch_status,
            last_error=source.last_error,
            status=source.status,
            last_checked_at=source.last_checked_at,
            validation_error=source.validation_error,
            detected_feed_type=source.detected_feed_type,
            example_item_count=source.example_item_count,
            has_images=source.has_images,
            has_descriptions=source.has_descriptions,
            has_dates=source.has_dates,
            notes=source.notes if args.notes is None else args.notes,
            group=source.group,
            created_at=source.created_at,
        ),
    )
    print(f"Updated source {saved.id}")
    return 0


def run_remove_source(args: argparse.Namespace) -> int:
    delete_user_source(args.db, args.id)
    print(f"Removed source {args.id}")
    return 0


def run_validate_source(args: argparse.Namespace) -> int:
    result = validate_source_to_db(args.db, args.id, timeout_seconds=args.timeout)
    print(
        f"{result.source_id}: {result.status} / {result.detected_feed_type or 'unknown'} / "
        f"{result.example_item_count} items"
    )
    if result.validation_error:
        print(f"error: {result.validation_error}")
    return 0


def run_validate_sources(args: argparse.Namespace) -> int:
    results = validate_all_sources_to_db(
        args.db, timeout_seconds=args.timeout, max_workers=args.workers
    )
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
        print(
            f"{result.source_id}: {result.status} / "
            f"{result.detected_feed_type or 'unknown'} / {result.example_item_count} items"
        )
    summary = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    print(f"Validation complete: {summary}")
    return 0


def run_sync(args: argparse.Namespace) -> int:
    sources = filter_sources(
        list_user_sources(args.db),
        category=args.category,
        source_ids=tuple(args.source_id),
    )
    if args.limit_per_source <= 0:
        raise FeedConfigError("--limit-per-source must be greater than zero.")
    run = ingest_sources(
        sources,
        db_path=args.db,
        run_dir=args.run_dir,
        timeout_seconds=args.timeout,
        limit_per_source=args.limit_per_source,
    )
    print(
        "Sync complete: "
        f"{run.total_written} new, {run.total_skipped} skipped, "
        f"{run.error_count} source errors."
    )
    for result in run.sources:
        if result.error:
            print(f"error {result.source_id}: {result.error}")
    return 0


def run_serve(args: argparse.Namespace) -> int:
    from feed_app.server import serve

    initialize_database(args.db)
    serve(
        host=args.host,
        port=args.port,
        db_path=args.db,
        run_dir=args.run_dir,
    )
    return 0


def filter_sources(
    sources: list[FeedSource], *, category: str | None, source_ids: tuple[str, ...]
) -> list[FeedSource]:
    wanted_ids = {source_id.strip().lower() for source_id in source_ids}
    return [
        source
        for source in sources
        if (not category or source.category == category)
        and (not wanted_ids or source.id in wanted_ids)
    ]


if __name__ == "__main__":
    raise SystemExit(main())
