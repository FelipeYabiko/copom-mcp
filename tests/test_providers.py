import json
import sys
from pathlib import Path

import httpx
import pytest

from copom_mcp.cli import main
from copom_mcp.extract import FEW_SHOT_ANSWER, ExtractionError
from copom_mcp.providers import PROVIDERS, CompatExtractor, ProviderUnavailable, RequestTooLarge, most_recent

sys.path.insert(0, str(Path(__file__).parent.parent))
from evals.run_extracao import cost_summary  # noqa: E402


def completion(content: str, finish: str = "stop") -> httpx.Response:
    body = {
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 4000, "completion_tokens": 900},
    }
    return httpx.Response(200, json=body)


def scripted(*responses: httpx.Response):
    """Cliente HTTP que devolve as respostas em ordem e guarda o que foi enviado."""
    queue, sent = list(responses), []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return queue.pop(0)

    return httpx.Client(transport=httpx.MockTransport(handler)), sent


def make(name, meetings, chunks, *responses, **kwargs):
    http, sent = scripted(*responses)
    sleeps: list[float] = []
    extractor = CompatExtractor(PROVIDERS[name], "chave", meetings, chunks, http=http, sleep=sleeps.append, **kwargs)
    return extractor, sent, sleeps


VALID = FEW_SHOT_ANSWER.model_dump_json()


def test_groq_sends_json_mode_without_example_and_records_trace(meetings, chunks, tmp_path):
    traces = tmp_path / "traces.jsonl"
    extractor, sent, _ = make("groq", meetings, chunks, completion(VALID), trace_path=traces)

    assert extractor.extract(280).tom == "duro"

    request = sent[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer chave"
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["response_format"] == {"type": "json_object"}
    assert body["reasoning_effort"] == "low"
    system = body["messages"][0]["content"]
    assert "JSON Schema" in system and "Exemplo resolvido" not in system  # no Groq gratuito o exemplo não cabe
    assert '<ata reuniao="280"' in body["messages"][1]["content"]
    trace = json.loads(traces.read_text())
    assert trace["model"] == "groq:openai/gpt-oss-120b" and trace["cost_usd"] == 0.0
    assert trace["input_tokens"] == 4000


def test_gemini_uses_json_schema_and_example(meetings, chunks):
    extractor, sent, _ = make("gemini", meetings, chunks, completion(f"```json\n{VALID}\n```"))

    assert extractor.extract(280).selic_nova == 13.75  # cercas de código são removidas

    body = json.loads(sent[0].content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"]["properties"]["tom"]["enum"] == ["duro", "neutro", "brando"]
    assert "Exemplo resolvido" in body["messages"][0]["content"]
    assert "reasoning_effort" not in body


def test_example_can_be_turned_on_or_off(meetings, chunks):
    groq, _, _ = make("groq", meetings, chunks, few_shot=True)
    gemini, _, _ = make("gemini", meetings, chunks, few_shot=False)
    assert "Exemplo resolvido" in groq.system
    assert "Exemplo resolvido" not in gemini.system


def test_invalid_answer_gets_one_corrective_retry(meetings, chunks):
    extractor, sent, _ = make("groq", meetings, chunks, completion('{"decisao": "corte"}'), completion(VALID))

    assert extractor.extract(280).decisao == "corte"

    retry = json.loads(sent[1].content)["messages"]
    assert retry[-2] == {"role": "assistant", "content": '{"decisao": "corte"}'}
    assert "não seguiu o schema" in retry[-1]["content"] and "selic_nova" in retry[-1]["content"]


def test_gives_up_after_two_invalid_answers(meetings, chunks):
    extractor, _, _ = make("groq", meetings, chunks, completion("{}"), completion("não sei"))
    with pytest.raises(ExtractionError, match="fora do schema"):
        extractor.extract(280)


def test_groq_json_validate_failed_goes_to_corrective_retry(meetings, chunks):
    failed = httpx.Response(400, json={"error": {"code": "json_validate_failed", "failed_generation": "{quase"}})
    extractor, sent, _ = make("groq", meetings, chunks, failed, completion(VALID))

    assert extractor.extract(280).unanime is True
    assert json.loads(sent[1].content)["messages"][-2]["content"] == "{quase"


def test_rate_limit_per_minute_waits_and_retries(meetings, chunks):
    limited = httpx.Response(429, text="Rate limit reached on tokens per minute (TPM). Please try again in 7.5s.")
    extractor, sent, sleeps = make("groq", meetings, chunks, limited, completion(VALID))

    extractor.extract(280)

    assert sleeps == [7.5] and len(sent) == 2


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, text="Rate limit reached on tokens per day (TPD): Limit 200000. Please try again in 7m1s."),
        httpx.Response(429, headers={"retry-after": "3600"}, text="Too many requests"),
        httpx.Response(401, json={"error": {"message": "Invalid API Key"}}),
        # Resposta real do Gemini para chave inválida: 400 e corpo em lista.
        httpx.Response(400, json=[{"error": {"code": 400, "message": "Please pass a valid API key"}}]),
    ],
)
def test_daily_quota_and_bad_key_stop_the_run(meetings, chunks, response):
    extractor, sent, sleeps = make("groq", meetings, chunks, response)
    with pytest.raises(ProviderUnavailable):
        extractor.extract(280)
    assert sleeps == [] and len(sent) == 1  # não insiste


def test_request_too_large_skips_the_meeting(meetings, chunks):
    extractor, _, _ = make("groq", meetings, chunks, httpx.Response(413, text="Request too large"))
    with pytest.raises(RequestTooLarge, match="limite de tokens"):
        extractor.extract(280)


def test_truncated_answer_is_an_error(meetings, chunks):
    extractor, _, _ = make("groq", meetings, chunks, completion('{"decisao":', finish="length"))
    with pytest.raises(ExtractionError, match="incompleta"):
        extractor.extract(280)


def test_openrouter_requires_a_model(meetings, chunks):
    with pytest.raises(ValueError, match="--modelo"):
        CompatExtractor(PROVIDERS["openrouter"], "chave", meetings, chunks)


def test_most_recent():
    assert most_recent([281, 279, 280], 2) == [280, 281]
    assert most_recent([281, 279], None) == [279, 281]


@pytest.mark.parametrize(
    "args",
    [
        ["extrair", "--provedor", "groq", "--lote"],
        ["extrair", "--provedor", "openrouter"],
        ["extrair", "--modelo", "x"],
        ["extrair", "--limite", "0"],
    ],
)
def test_cli_rejects_invalid_combinations(args):
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2


def test_cli_needs_the_provider_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert main(["extrair", "--provedor", "groq"]) == 2


@pytest.mark.parametrize(
    ("error", "expected_attempts", "expected_status"),
    [
        (ExtractionError, 3, 1),  # três seguidas: algo está errado, para
        (RequestTooLarge, 8, 0),  # ata longa demais é esperado: pula e segue
    ],
)
def test_cli_stops_after_consecutive_failures(
    monkeypatch, tmp_path, meetings, chunks, error, expected_attempts, expected_status
):
    attempted = []

    class AlwaysFails:
        label = "groq:teste"

        def __init__(self, *args, **kwargs):
            pass

        def extract(self, number):
            attempted.append(number)
            raise error(f"Reunião {number}: erro")

    many = [
        *meetings,
        *(type(meetings[1])(n, "2026-01-01", "2026-01-02", f"{n}ª", "", 15.0, 15.0) for n in range(1, 6)),
    ]
    monkeypatch.setenv("GROQ_API_KEY", "chave")
    monkeypatch.setattr("copom_mcp.cli.load_corpus", lambda: (many, chunks))
    monkeypatch.setattr("copom_mcp.providers.CompatExtractor", AlwaysFails)

    assert main(["extrair", "--provedor", "groq", "--saida", str(tmp_path / "a.json")]) == expected_status
    assert len(attempted) == expected_attempts


def test_cost_summary_separates_models():
    def trace(model, cost):
        return dict(
            model=model,
            input_tokens=100,
            output_tokens=10,
            cache_read_tokens=0,
            cache_write_tokens=0,
            latency_s=1.0,
            cost_usd=cost,
        )

    summary = cost_summary([trace("claude-opus-5-5", 0.02), trace("groq:openai/gpt-oss-120b", 0.0)])
    assert summary["claude-opus-5-5"]["custo_total_usd"] == 0.02
    assert summary["groq:openai/gpt-oss-120b"]["chamadas"] == 1
