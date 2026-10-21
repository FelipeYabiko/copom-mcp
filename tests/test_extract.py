import json
from types import SimpleNamespace

import pytest

from copom_mcp.corpus import Chunk
from copom_mcp.extract import (
    FEW_SHOT_ANSWER,
    FEW_SHOT_MEETING,
    AnaliseAta,
    ExtractionError,
    Extractor,
    estimate_cost,
)


@pytest.fixture
def corpus(meetings, chunks):
    # O exemplo do few-shot (281ª) precisa existir no corpus.
    extra = [Chunk(FEW_SHOT_MEETING, "2026-09-16", "D) Decisão", 18, "O Copom decidiu reduzir para 13,75% a.a.")]
    return meetings, chunks + extra


def usage(**kw):
    base = dict(input_tokens=1000, output_tokens=500, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return SimpleNamespace(**{**base, **kw})


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def parsed(output, stop_reason="end_turn"):
    return SimpleNamespace(parsed_output=output, stop_reason=stop_reason, usage=usage(), model="claude-opus-5-5")


def test_extract_sends_cached_few_shot_and_schema(corpus, tmp_path):
    client = FakeClient([parsed(FEW_SHOT_ANSWER)])
    extractor = Extractor(client, *corpus, trace_path=tmp_path / "traces.jsonl")

    result = extractor.extract(280)

    assert result.decisao == "corte"
    call = client.calls[0]
    assert call["output_format"] is AnaliseAta
    assert call["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "Exemplo resolvido" in call["system"][-1]["text"]
    assert '<ata reuniao="280"' in call["messages"][0]["content"]
    assert call["fallbacks"] == "default"
    trace = json.loads((tmp_path / "traces.jsonl").read_text())
    assert trace["meeting"] == 280 and trace["cost_usd"] == pytest.approx(0.014)


def test_extract_raises_on_refusal_and_truncation(corpus):
    client = FakeClient([parsed(None, "refusal"), parsed(None, "max_tokens")])
    extractor = Extractor(client, *corpus)
    with pytest.raises(ExtractionError, match="recusou"):
        extractor.extract(280)
    with pytest.raises(ExtractionError, match="incompleta"):
        extractor.extract(280)


def test_batch_requests_use_raw_json_schema(corpus):
    extractor = Extractor(None, *corpus)
    [request] = extractor.batch_requests([280])
    assert request["custom_id"] == "ata-280"
    fmt = request["params"]["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert "fallbacks" not in request["params"]  # a Batch API não aceita fallbacks


def test_parse_batch_result():
    message = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text=FEW_SHOT_ANSWER.model_dump_json())]
    )
    ok = SimpleNamespace(custom_id="ata-280", result=SimpleNamespace(type="succeeded", message=message))
    failed = SimpleNamespace(custom_id="ata-279", result=SimpleNamespace(type="errored"))
    assert Extractor.parse_batch_result(ok)[1].tom == "duro"
    assert Extractor.parse_batch_result(failed) == (279, None, "errored")


def test_cost_estimate_includes_cache_and_batch_discount():
    u = usage(input_tokens=1_000_000, output_tokens=0, cache_read_input_tokens=1_000_000)
    assert estimate_cost(u) == pytest.approx(4.20)
    assert estimate_cost(u, batch=True) == pytest.approx(2.10)
