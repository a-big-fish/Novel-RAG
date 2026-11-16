"""stdio MCP server for Novel-RAG.

The MCP surface hides retrieval-query construction from the consuming agent.
An agent only states the scene it wants to write; the server plans the query,
searches the book set and returns an assembled prompt or structured fragments.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from anyio import to_thread
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from app.clients.llm_client import build_llm_client
from app.clients.ollama_client import OllamaClient
from app.clients.rerank_client import RerankClient
from app.config import Settings, get_settings, read_rerank_enabled
from app.db.postgres import PostgresDatabase, PostgresRepository
from app.db.qdrant import QdrantAdapter
from app.mcp_guide import INSTRUCTIONS, USAGE_GUIDE
from app.services import request_planner
from app.services.multi_search import MultiBookSearcher
from app.services.query_parser import QueryParser
from app.services.retriever import Retriever
from app.services.writing_reference import WritingReferenceService

mcp = MCPServer(
    name=get_settings().mcp_server_name,
    instructions=INSTRUCTIONS,
    version=get_settings().app_version,
)


def _tool_error(exc: Exception) -> ToolError:
    """Expose anticipated failures to the client model instead of a crash."""

    return ToolError(f"{type(exc).__name__}: {exc}")


async def _guarded(call: Callable[[], Any]) -> Any:
    try:
        return await to_thread.run_sync(call)
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 - convert into an MCP tool error
        raise _tool_error(exc) from exc


def _guarded_sync(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _tool_error(exc) from exc


def _effective_settings(use_rerank: bool) -> Settings:
    base = get_settings()
    if not use_rerank:
        return base.model_copy(update={"rerank_enabled": False})
    try:
        enabled = read_rerank_enabled()
    except ValueError:
        enabled = bool(base.rerank_enabled)
    return base.model_copy(update={"rerank_enabled": enabled})


@contextmanager
def _repository() -> Iterator[PostgresRepository]:
    """Database-only context for read tools; no LLM or Ollama required."""

    settings = get_settings()
    database = PostgresDatabase.from_settings(settings)
    try:
        yield PostgresRepository(database)
    finally:
        database.dispose()


@contextmanager
def _search_services(use_rerank: bool) -> Iterator[
    tuple[PostgresRepository, WritingReferenceService, Settings]
]:
    settings = _effective_settings(use_rerank)
    database = PostgresDatabase.from_settings(settings)
    repository = PostgresRepository(database)
    qdrant = QdrantAdapter(settings=settings)
    ollama = OllamaClient(settings=settings)
    llm = None
    reranker = None
    try:
        llm = build_llm_client(settings)
        if settings.rerank_enabled:
            reranker = RerankClient(
                base_url=settings.ollama_url,
                model=settings.ollama_rerank_model,
                timeout_seconds=settings.rerank_timeout_seconds,
                concurrency=settings.rerank_concurrency,
            )
        parser = QueryParser(repository, llm, settings)
        retriever = Retriever(repository, qdrant, ollama, parser, settings)
        searcher = MultiBookSearcher(repository, retriever, settings, reranker)
        yield repository, WritingReferenceService(repository, searcher, settings), settings
    finally:
        if reranker is not None:
            reranker.close()
        if llm is not None:
            llm.close()
        ollama.close()
        qdrant.close()
        database.dispose()


def _search_sync(
    *,
    writing_goal: str,
    outline_context: str,
    constraints: dict[str, Any] | None,
    book_ids: list[int] | None,
    output: str,
    budget_chars: int | None,
    max_scenes: int | None,
    book_quota: int | None,
    use_rerank: bool,
    query: str | None = None,
) -> dict[str, Any]:
    with _search_services(use_rerank) as (repository, service, _settings):
        return service.search(
            writing_goal=writing_goal,
            outline_context=outline_context,
            constraints=constraints,
            book_ids=book_ids,
            output=output,
            budget_chars=budget_chars,
            max_scenes=max_scenes,
            book_quota=book_quota,
            query=query,
        )


def _scene_sync(book_id: int, version: int, scene_id: int) -> dict[str, Any]:
    with _repository() as repository:
        scene = repository.get_scene(scene_id)
        if (scene is None or int(scene["book_id"]) != book_id
                or int(scene["version"]) != version):
            raise ValueError("scene not found in requested book/version")
        return {
            "book_id": book_id,
            "version": version,
            "scene_id": int(scene["id"]),
            "scene_index_in_book": int(scene["scene_index_in_book"]),
            "chapter_start_index": int(scene["chapter_start_index"]),
            "chapter_end_index": int(scene["chapter_end_index"]),
            "char_count": int(scene["char_count"]),
            "reference_status": scene["reference_status"],
            "reference_score": scene.get("reference_score"),
            "summary": scene.get("summary"),
            "style_summary": scene.get("style_summary"),
            "usage_hint": scene.get("usage_hint"),
            "scene_type": scene.get("scene_type"),
            "technique": scene.get("technique"),
            "style_tags": scene.get("style_tags"),
            "emotion_tags": scene.get("emotion_tags"),
            "key_images": scene.get("key_images"),
            "narrative_func": scene.get("narrative_func"),
            "text": scene.get("text"),
        }


def _books_sync() -> list[dict[str, Any]]:
    with _repository() as repository:
        return [
            {
                "book_id": int(book["id"]),
                "title": book["title"],
                "author": book["author"],
                "status": book["status"],
                "current_version": int(book["current_version"] or 0),
                "total_scenes": int(book["total_scenes"] or 0),
                "selected_scenes": int(book["selected_scenes"] or 0),
            }
            for book in repository.list_books()
        ]


def _status_sync() -> dict[str, Any]:
    settings = get_settings()
    status: dict[str, Any] = {
        "postgres": "unknown",
        "qdrant": "unknown",
        "ollama": "unknown",
        "llm": "configured" if (
            settings.llm_base_url and settings.llm_model
        ) else "missing configuration",
        "index_worker_enabled": settings.index_worker_enabled,
        "ready_books": 0,
        "books": [],
    }
    try:
        database = PostgresDatabase.from_settings(settings)
        try:
            database.ping()
            repository = PostgresRepository(database)
            books = repository.list_books()
            status["postgres"] = "ok"
            status["ready_books"] = sum(
                1 for book in books
                if str(book["status"]) == "ready"
                and int(book["current_version"] or 0) > 0
            )
            status["books"] = [
                {"book_id": int(book["id"]), "title": book["title"],
                 "status": book["status"],
                 "current_version": int(book["current_version"] or 0)}
                for book in books
            ]
        finally:
            database.dispose()
    except Exception as exc:  # noqa: BLE001
        status["postgres"] = f"error: {exc}"
    try:
        qdrant = QdrantAdapter(settings=settings)
        try:
            qdrant.ping()
            status["qdrant"] = "ok"
        finally:
            qdrant.close()
    except Exception as exc:  # noqa: BLE001
        status["qdrant"] = f"error: {exc}"
    try:
        ollama = OllamaClient(settings=settings)
        try:
            ollama.ping()
            status["ollama"] = "ok"
        finally:
            ollama.close()
    except Exception as exc:  # noqa: BLE001
        status["ollama"] = f"error: {exc}"
    return status


@mcp.tool()
async def search_writing_references(
    writing_goal: str,
    outline_context: str = "",
    constraints: dict[str, Any] | None = None,
    book_ids: list[int] | None = None,
    output: str = "prompt",
    budget_chars: int | None = None,
    max_scenes: int | None = None,
    book_quota: int | None = None,
    use_rerank: bool = False,
) -> dict[str, Any]:
    """Find writing references for a scene you want to write.

    Provide the writing goal and optional outline/constraints; the server builds
    the retrieval query, searches the selected books and returns an assembled
    prompt (default) and/or structured fragments. It never generates prose.
    """

    return await _guarded(lambda: _search_sync(
        writing_goal=writing_goal,
        outline_context=outline_context,
        constraints=constraints,
        book_ids=book_ids,
        output=output,
        budget_chars=budget_chars,
        max_scenes=max_scenes,
        book_quota=book_quota,
        use_rerank=use_rerank,
    ))


@mcp.tool()
def plan_writing_query(
    writing_goal: str,
    outline_context: str = "",
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Preview the retrieval query built from a writing brief without searching."""

    return _guarded_sync(lambda: request_planner.plan_writing_query(
        writing_goal=writing_goal,
        outline_context=outline_context,
        constraints=constraints,
        max_chars=get_settings().query_max_chars,
    ))


@mcp.tool()
async def build_reference_prompt(
    query: str,
    task: str = "",
    book_ids: list[int] | None = None,
    output: str = "prompt",
    budget_chars: int | None = None,
    max_scenes: int | None = None,
    book_quota: int | None = None,
    use_rerank: bool = False,
) -> dict[str, Any]:
    """Advanced: use a ready-made retrieval query and return assembled output."""

    return await _guarded(lambda: _search_sync(
        writing_goal=task or query,
        outline_context="",
        constraints=None,
        book_ids=book_ids,
        output=output,
        budget_chars=budget_chars,
        max_scenes=max_scenes,
        book_quota=book_quota,
        use_rerank=use_rerank,
        query=query,
    ))


@mcp.tool()
async def get_scene(book_id: int, version: int, scene_id: int) -> dict[str, Any]:
    """Read one scene with its annotation from the archive layer."""

    return await _guarded(lambda: _scene_sync(book_id, version, scene_id))


@mcp.tool()
async def list_books() -> dict[str, Any]:
    """List indexed books and their current versions."""

    books = await _guarded(_books_sync)
    return {"count": len(books), "books": books}


@mcp.tool()
async def get_service_status() -> dict[str, Any]:
    """Check database, Qdrant, Ollama and LLM configuration before searching."""

    return await _guarded(_status_sync)


@mcp.tool()
def get_usage_guide() -> str:
    """Return the guide that explains how to phrase requests to this server."""

    return USAGE_GUIDE


@mcp.resource("novel-rag://usage-guide", mime_type="text/markdown")
def usage_guide_resource() -> str:
    """How to call Novel-RAG and use the returned prompt or fragments."""

    return USAGE_GUIDE


@mcp.prompt()
def writing_reference_brief(writing_goal: str) -> str:
    """Guidance for an agent preparing a Novel-RAG request."""

    return (
        "调用 Novel-RAG 的 `search_writing_references`，只提交你想写的内容，"
        "不要自己拼检索标签。\n\n"
        f"写作目标：{writing_goal}\n\n"
        "建议参数：writing_goal（上述目标）、outline_context（该场景在细纲中的上下文）、"
        "constraints（情绪/句式/视角/避免项）。默认 output=prompt，"
        "拿到 prompt.text 后直接交给写作模型；服务状态可用 get_service_status 检查。\n\n"
        f"完整说明：\n{USAGE_GUIDE}"
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
