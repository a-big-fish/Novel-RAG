from app.services.fusion import reciprocal_rank_fusion


def test_rrf_combines_ranks_without_scores_or_weights():
    routes = {
        "a": {"items": [{"scene_id": 1, "score": 0.01}, {"scene_id": 2, "score": 99}]},
        "b": {"items": [{"scene_id": 2, "score": -5}, {"scene_id": 1, "score": 90}]},
        "c": {"items": []},
    }
    result = reciprocal_rank_fusion(routes, k=60, limit=2)
    assert [item["scene_id"] for item in result] == [1, 2]
    assert result[0]["route_ranks"] == {"a": 1, "b": 2}
    assert result[0]["rrf_contributions"]["a"] == 1 / 61


def test_rrf_deduplicates_same_scene_within_route():
    result = reciprocal_rank_fusion({
        "a": {"items": [{"scene_id": 1}, {"scene_id": 1}]}
    })
    assert result[0]["rrf_score"] == 1 / 61
