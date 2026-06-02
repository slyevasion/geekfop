from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from feed_app.defaults import CATEGORIES, DEFAULT_USER_ID
from feed_app.ingest import ingest_sources
from feed_app.models import FeedConfigError, FeedSource, IngestRun
from feed_app.repository import FeedRepository, repository_for_database_url
from feed_app.server import APP_HTML, source_from_payload, source_placeholder
from feed_app.settings import AppSettings, get_settings
from feed_app.validator import ValidationResult, validate_source

DEFAULT_RUN_DIR_SENTINEL = object()


def create_app(
    settings: AppSettings | None = None,
    repository: FeedRepository | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    repo = repository or repository_for_database_url(
        app_settings.database_url,
        app_settings.sqlite_db_path,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        repo.initialize()
        yield

    app = FastAPI(title="Recon Feed", version="0.1.0", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.repository = repo

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return APP_HTML

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "ok": True,
            "managed_database": app_settings.uses_managed_database,
            "daily_cron_path": "/api/cron/sync",
        }

    @app.get("/api/categories", dependencies=[Depends(require_app_access)])
    def categories() -> dict[str, object]:
        return {"categories": list(CATEGORIES)}

    @app.get("/api/sources", dependencies=[Depends(require_app_access)])
    def sources(repo: Annotated[FeedRepository, Depends(get_repo)]) -> dict[str, object]:
        return {
            "sources": [
                source.to_dict()
                for source in repo.list_user_sources(user_id=app_settings.user_id)
            ]
        }

    @app.get("/api/items", dependencies=[Depends(require_app_access)])
    def items(
        repo: Annotated[FeedRepository, Depends(get_repo)],
        category: str | None = None,
        source_id: str | None = None,
        q: str | None = None,
        saved: str | None = None,
        limit: int = Query(200, ge=1, le=500),
    ) -> dict[str, object]:
        saved_only = (saved or "").lower() in {"1", "true", "yes"}
        feed_items = repo.list_feed_items(
            user_id=app_settings.user_id,
            category=category,
            source_id=source_id,
            search=q,
            saved_only=saved_only,
            limit=limit,
        )
        placeholders = []
        if category and not saved_only:
            placeholders = [
                source_placeholder(source)
                for source in repo.list_user_sources(user_id=app_settings.user_id)
                if source.category == category
                and source.status in {"failed", "needs_feed_url", "unsupported_v1"}
            ]
        return {
            "items": [item.to_dict() for item in feed_items],
            "placeholders": placeholders,
            "total": len(feed_items),
        }

    @app.post("/api/sync", dependencies=[Depends(require_app_access)])
    def sync(repo: Annotated[FeedRepository, Depends(get_repo)]) -> dict[str, object]:
        run = run_sync(repo, app_settings)
        return {"run": run.to_dict()}

    @app.get("/api/cron/sync")
    @app.post("/api/cron/sync")
    def cron_sync(
        request: Request,
        repo: Annotated[FeedRepository, Depends(get_repo)],
    ) -> dict[str, object]:
        require_cron_access(request)
        run = run_sync(
            repo,
            app_settings,
            category=app_settings.sync_category,
            source_ids=app_settings.sync_source_ids,
            run_dir=None,
            output_path=(
                "managed-db" if app_settings.database_url else str(app_settings.sqlite_db_path)
            ),
        )
        return {"run": run.to_dict()}

    @app.post("/api/sources", dependencies=[Depends(require_app_access)])
    async def add_source(
        request: Request,
        repo: Annotated[FeedRepository, Depends(get_repo)],
    ) -> dict[str, object]:
        source = source_from_payload(await read_object_body(request))
        saved = repo.upsert_user_source(source, user_id=app_settings.user_id)
        return {"source": saved.to_dict()}

    @app.put("/api/sources/{source_id}", dependencies=[Depends(require_app_access)])
    async def update_source(
        source_id: str,
        request: Request,
        repo: Annotated[FeedRepository, Depends(get_repo)],
    ) -> dict[str, object]:
        current = repo.get_user_source(source_id, user_id=app_settings.user_id)
        data = current.to_dict()
        data.update(await read_object_body(request))
        data["id"] = source_id
        saved = repo.upsert_user_source(source_from_payload(data), user_id=app_settings.user_id)
        return {"source": saved.to_dict()}

    @app.delete("/api/sources/{source_id}", dependencies=[Depends(require_app_access)])
    def delete_source(
        source_id: str,
        repo: Annotated[FeedRepository, Depends(get_repo)],
    ) -> dict[str, object]:
        repo.delete_user_source(source_id, user_id=app_settings.user_id)
        return {"removed": source_id}

    @app.post("/api/sources/{source_id}/toggle", dependencies=[Depends(require_app_access)])
    def toggle_source(
        source_id: str,
        repo: Annotated[FeedRepository, Depends(get_repo)],
    ) -> dict[str, object]:
        source = repo.toggle_user_source(source_id, user_id=app_settings.user_id)
        return {"source": source.to_dict()}

    @app.post("/api/sources/{source_id}/validate", dependencies=[Depends(require_app_access)])
    def validate_one_source(
        source_id: str,
        repo: Annotated[FeedRepository, Depends(get_repo)],
    ) -> dict[str, object]:
        result = validate_source_to_repo(
            repo,
            source_id=source_id,
            user_id=app_settings.user_id,
            timeout_seconds=app_settings.sync_timeout_seconds,
        )
        return {"result": result.to_dict()}

    @app.post("/api/validate-sources", dependencies=[Depends(require_app_access)])
    def validate_sources(repo: Annotated[FeedRepository, Depends(get_repo)]) -> dict[str, object]:
        results = validate_all_sources_to_repo(
            repo,
            user_id=app_settings.user_id,
            timeout_seconds=app_settings.sync_timeout_seconds,
        )
        return {"results": [result.to_dict() for result in results]}

    @app.post("/api/sources/{source_id}/refresh", dependencies=[Depends(require_app_access)])
    def refresh_source(
        source_id: str,
        repo: Annotated[FeedRepository, Depends(get_repo)],
    ) -> dict[str, object]:
        run = run_sync(repo, app_settings, source_ids=(source_id.strip().lower(),))
        return {"run": run.to_dict(), "message": sync_message(run)}

    @app.post("/api/refresh", dependencies=[Depends(require_app_access)])
    def refresh_all(repo: Annotated[FeedRepository, Depends(get_repo)]) -> dict[str, object]:
        run = run_sync(repo, app_settings)
        return {"run": run.to_dict(), "message": sync_message(run)}

    @app.exception_handler(FeedConfigError)
    def feed_config_error_handler(_request: Request, exc: FeedConfigError):
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.exception_handler(ValueError)
    def value_error_handler(_request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"error": str(exc)})

    return app


def get_repo(request: Request) -> FeedRepository:
    repo = request.app.state.repository
    repo.initialize()
    return repo


def require_app_access(request: Request) -> None:
    settings: AppSettings = request.app.state.settings
    if not settings.app_access_token:
        return
    token = request.headers.get("x-feed-app-token", "").strip()
    if token != settings.app_access_token:
        raise HTTPException(status_code=401, detail="Missing or invalid access token.")


def require_cron_access(request: Request) -> None:
    settings: AppSettings = request.app.state.settings
    if not settings.cron_secret:
        return
    authorization = request.headers.get("authorization", "")
    bearer = authorization.removeprefix("Bearer ").strip()
    header_secret = request.headers.get("x-cron-secret", "").strip()
    if settings.cron_secret not in {bearer, header_secret}:
        raise HTTPException(status_code=401, detail="Missing or invalid cron secret.")


async def read_object_body(request: Request) -> dict[str, object]:
    raw = await request.body()
    if len(raw) > 1_000_000:
        raise ValueError("Request body too large.")
    data = json.loads(raw.decode("utf-8") if raw else "{}")
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object.")
    return data


def run_sync(
    repo: FeedRepository,
    settings: AppSettings,
    *,
    category: str | None = None,
    source_ids: tuple[str, ...] = (),
    run_dir: Path | None | object = DEFAULT_RUN_DIR_SENTINEL,
    output_path: str | None = None,
) -> IngestRun:
    sources = filter_sources(
        repo.list_user_sources(user_id=settings.user_id),
        category=category,
        source_ids=source_ids,
    )
    if settings.sync_limit_per_source <= 0:
        raise FeedConfigError("SYNC_LIMIT_PER_SOURCE must be greater than zero.")
    chosen_run_dir = settings.run_dir if run_dir is DEFAULT_RUN_DIR_SENTINEL else run_dir
    return ingest_sources(
        sources,
        db_path=settings.sqlite_db_path,
        run_dir=chosen_run_dir,
        user_id=settings.user_id,
        timeout_seconds=settings.sync_timeout_seconds,
        limit_per_source=settings.sync_limit_per_source,
        append_items_func=lambda _db_path, source, items, user_id: repo.append_feed_items(
            source, items, user_id=user_id
        ),
        record_error_func=lambda _db_path, source, error, user_id: repo.record_source_error(
            source, error, user_id=user_id
        ),
        output_path=output_path,
    )


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


def validate_source_to_repo(
    repo: FeedRepository,
    *,
    source_id: str | None = None,
    source: FeedSource | None = None,
    user_id: str = DEFAULT_USER_ID,
    timeout_seconds: float = 12,
) -> ValidationResult:
    target = source or repo.get_user_source(source_id or "", user_id=user_id)
    result = validate_source(target, timeout_seconds=timeout_seconds)
    repo.update_source_validation(
        target.id,
        user_id=user_id,
        status=result.status,
        validation_error=result.validation_error,
        detected_feed_type=result.detected_feed_type,
        example_item_count=result.example_item_count,
        has_images=result.has_images,
        has_descriptions=result.has_descriptions,
        has_dates=result.has_dates,
        last_checked_at=result.last_checked_at,
    )
    return result


def validate_all_sources_to_repo(
    repo: FeedRepository,
    *,
    user_id: str = DEFAULT_USER_ID,
    timeout_seconds: float = 12,
    max_workers: int = 8,
) -> list[ValidationResult]:
    sources = repo.list_user_sources(user_id=user_id)
    results: list[ValidationResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                validate_source_to_repo,
                repo,
                source=source,
                user_id=user_id,
                timeout_seconds=timeout_seconds,
            ): source.id
            for source in sources
        }
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda result: result.source_id)


def sync_message(run: IngestRun) -> str:
    return (
        f"Sync: {run.total_written} new, {run.total_skipped} skipped, "
        f"{run.error_count} errors"
    )


app = create_app()
