"""Batch API: um lote criado (e pago) nunca é criado de novo por causa de uma interrupção."""

import json
from types import SimpleNamespace

import pytest

from copom_mcp import cli
from copom_mcp.extract import FEW_SHOT_ANSWER


class FakeBatches:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.created = []

    def create(self, requests):
        self.created.append([r["custom_id"] for r in requests])
        return SimpleNamespace(id="lote_1")

    def retrieve(self, job_id):
        status = self.statuses.pop(0)
        if isinstance(status, BaseException):
            raise status
        return SimpleNamespace(processing_status=status)

    def results(self, job_id):
        message = SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=FEW_SHOT_ANSWER.model_dump_json())],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            model="claude-opus-5-5",
        )
        return [SimpleNamespace(custom_id="ata-280", result=SimpleNamespace(type="succeeded", message=message))]


@pytest.fixture
def setup(monkeypatch, tmp_path, meetings, chunks):
    batches = FakeBatches([])
    client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
    monkeypatch.setattr("anthropic.Anthropic", lambda: client)
    monkeypatch.setattr(cli, "load_corpus", lambda: (meetings, chunks))
    monkeypatch.setattr(cli, "BATCH_STATE_PATH", tmp_path / "lote-pendente.json")
    monkeypatch.setattr(cli, "TRACE_PATH", tmp_path / "traces.jsonl")
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    return batches, tmp_path


def test_interrupted_batch_is_resumed_not_recreated(setup):
    batches, tmp_path = setup
    output = tmp_path / "analises.json"

    # 1ª execução: cria o lote e é interrompida enquanto espera.
    batches.statuses = ["in_progress", KeyboardInterrupt()]
    assert cli.main(["extrair", "280", "--lote", "--saida", str(output)]) == 130
    assert batches.created == [["ata-280"]]
    assert json.loads(cli.BATCH_STATE_PATH.read_text()) == {"id": "lote_1", "reunioes": [280]}

    # 2ª execução: retoma o mesmo lote, sem criar (e pagar) outro.
    batches.statuses = ["in_progress", "ended"]
    assert cli.main(["extrair", "280", "--lote", "--saida", str(output)]) == 0
    assert batches.created == [["ata-280"]]
    assert json.loads(output.read_text())["280"]["tom"] == "duro"
    assert not cli.BATCH_STATE_PATH.exists()


def test_normal_mode_refuses_to_run_while_a_batch_is_pending(setup):
    _, tmp_path = setup
    cli.BATCH_STATE_PATH.write_text(json.dumps({"id": "lote_1", "reunioes": [280]}))

    assert cli.main(["extrair", "280", "--saida", str(tmp_path / "analises.json")]) == 1
    assert not (tmp_path / "analises.json").exists()


def test_nothing_to_do_creates_no_batch(setup):
    batches, tmp_path = setup
    output = tmp_path / "analises.json"
    output.write_text(json.dumps({str(n): FEW_SHOT_ANSWER.model_dump() for n in (279, 280, 281)}))

    assert cli.main(["extrair", "--lote", "--saida", str(output)]) == 0
    assert batches.created == []
