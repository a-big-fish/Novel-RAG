from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from queue import Queue
from threading import Thread
from typing import Any

from fastapi.responses import StreamingResponse

from app.services.retriever import ProgressCallback


def search_stream(
    run: Callable[[ProgressCallback], dict[str, Any]],
) -> StreamingResponse:
    """Stream real pipeline transitions and the final search response as NDJSON."""

    def events() -> Iterator[str]:
        pending: Queue[dict[str, Any] | None] = Queue()

        def publish(stage: str, details: dict[str, Any]) -> None:
            pending.put({"type": "progress", "stage": stage, **details})

        def worker() -> None:
            try:
                pending.put({"type": "result", "data": run(publish)})
            except Exception as exc:
                pending.put({"type": "error", "message": str(exc)})
            finally:
                pending.put(None)

        Thread(target=worker, daemon=True, name="dashboard-search-stream").start()
        while (event := pending.get()) is not None:
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(
        events(), media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
