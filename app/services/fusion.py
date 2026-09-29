from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def reciprocal_rank_fusion(
    routes: Mapping[str, dict[str, Any]], *, k: int = 60, limit: int = 20,
) -> list[dict[str, Any]]:
    if k <= 0 or limit <= 0:
        raise ValueError("k and limit must be positive")
    candidates: dict[int, dict[str, Any]] = {}
    for route_name, route in routes.items():
        seen: set[int] = set()
        for rank, item in enumerate(route["items"], start=1):
            scene_id = int(item["scene_id"])
            if scene_id in seen:
                continue
            seen.add(scene_id)
            candidate = candidates.setdefault(scene_id, {
                "scene_id": scene_id, "rrf_score": 0.0,
                "route_ranks": {}, "rrf_contributions": {},
            })
            contribution = 1.0 / (k + rank)
            candidate["route_ranks"][route_name] = rank
            candidate["rrf_contributions"][route_name] = contribution
            candidate["rrf_score"] += contribution
    return sorted(
        candidates.values(),
        key=lambda c: (
            -c["rrf_score"], -len(c["route_ranks"]),
            min(c["route_ranks"].values()), c["scene_id"],
        ),
    )[:limit]
