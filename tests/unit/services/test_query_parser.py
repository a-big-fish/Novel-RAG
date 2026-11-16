from datetime import UTC, datetime

from app.config import Settings
from app.services.query_parser import QueryParser


class FakeRepository:
    def __init__(self):
        self.cache = {}
        self.rows = {}

    def get_query_cache(self, key):
        return self.cache.get(key)

    def get_query_cache_row(self, key):
        return self.rows.get(key)

    def put_query_cache(self, *, input_hash, output_json, **_):
        self.cache[input_hash] = output_json
        self.rows[input_hash] = {
            "output_json": output_json,
            "created_at": datetime.now(UTC),
        }


class FakeLLM:
    model = "fake"

    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.calls = 0
        self.options = []

    def request_json(self, **_):
        return self.request_json_with_options(**_)

    def request_json_with_options(self, *, options=None, **_):
        self.calls += 1
        self.options.append(options)
        if self.error:
            raise self.error
        return self.output


def test_query_parser_maps_known_tags_and_uses_persistent_cache():
    repository = FakeRepository()
    llm = FakeLLM({
        "summary_query": "雨夜追逐",
        "scene_type": "动作场景",
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


def test_query_parser_caches_fallback_after_failure():
    repository = FakeRepository()
    llm = FakeLLM(error=TimeoutError("upstream timeout"))
    parser = QueryParser(repository, llm, Settings())
    first = parser.parse("雨夜追逐")
    second = parser.parse("雨夜追逐")
    assert first.fallback is True
    assert second.fallback is True
    assert second.cache_hit is True
    assert llm.calls == 1
    assert repository.cache


def test_query_parser_falls_back_on_invalid_output():
    repository = FakeRepository()
    parser = QueryParser(repository, FakeLLM({"technique": {"invalid": True}}), Settings())
    result = parser.parse("雨夜追逐")
    assert result.fallback is True
    assert result.summary_query == ""


def test_query_parser_uses_latency_first_options():
    repository = FakeRepository()
    llm = FakeLLM({"summary_query": "x"})
    settings = Settings(
        query_llm_model="fast-model",
        query_llm_max_tokens=256,
        query_llm_reasoning_effort="minimal",
        query_llm_timeout_seconds=12,
        query_llm_max_retries=0,
        query_llm_json_response_format=False,
    )
    QueryParser(repository, llm, settings).parse("雨夜追逐")
    options = llm.options[0]
    assert options.model == "fast-model"
    assert options.max_tokens == 256
    assert options.reasoning_effort == "minimal"
    assert options.timeout_seconds == 12
    assert options.max_retries == 0
    assert options.json_response_format is False
