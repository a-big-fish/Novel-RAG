from app.config import Settings
from app.services.query_parser import QueryParser


class FakeRepository:
    def __init__(self):
        self.cache = {}

    def get_query_cache(self, key):
        return self.cache.get(key)

    def put_query_cache(self, *, input_hash, output_json, **_):
        self.cache[input_hash] = output_json


class FakeLLM:
    model = "fake"

    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.calls = 0

    def request_json(self, **_):
        self.calls += 1
        if self.error:
            raise self.error
        return self.output


def test_query_parser_maps_known_tags_and_uses_persistent_cache():
    repository = FakeRepository()
    llm = FakeLLM({
        "summary_query": "雨夜追逐",
        "scene_type": ["动作场景", "unknown"],
        "style_tags": ["短句"],
    })
    parser = QueryParser(repository, llm, Settings())
    first = parser.parse(" 雨夜追逐，短句 ")
    second = parser.parse("雨夜追逐，短句")
    assert first.raw_intent == "雨夜追逐，短句"
    assert first.scene_type == ["action"]
    assert first.style_tags == ["short_sentence"]
    assert second.cache_hit is True
    assert llm.calls == 1


def test_query_parser_falls_back_on_invalid_output_without_caching():
    repository = FakeRepository()
    parser = QueryParser(repository, FakeLLM({"technique": "invalid"}), Settings())
    result = parser.parse("雨夜追逐")
    assert result.fallback is True
    assert result.summary_query == ""
    assert repository.cache == {}
