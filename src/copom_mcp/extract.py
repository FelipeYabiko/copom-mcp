"""Extração estruturada: o Claude lê uma ata e devolve um JSON validado com decisão, tom e riscos.

Técnicas usadas aqui:
- Saída estruturada: o schema vem de um modelo Pydantic e a API garante JSON válido.
- Few-shot: o prompt de sistema traz uma ata real já analisada como exemplo.
- Prompt caching: esse prefixo grande é idêntico em todas as chamadas e fica em cache.
- Batch API: para processar todas as atas com 50% de desconto.
- Rastreamento: cada chamada registra tokens, custo estimado e latência.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .corpus import Chunk, Meeting

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Dólares por milhão de tokens (Claude Opus 5.5). Escrita em cache custa 1,25x a entrada.
PRICES = {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00}
FEW_SHOT_MEETING = 281


class AnaliseAta(BaseModel):
    decisao: Literal["alta", "corte", "manutenção"]
    selic_nova: float = Field(description="Meta Selic decidida, em % ao ano")
    variacao_pontos_base: int = Field(description="Negativo para corte. Ex.: -25")
    unanime: bool
    votos_divergentes: list[str] = Field(description="Quem votou diferente e o que defendeu; vazio se unânime")
    tom: Literal["duro", "neutro", "brando"] = Field(
        description="duro (hawkish): preocupação com inflação, viés de aperto; brando (dovish): viés de afrouxamento"
    )
    justificativa_tom: str
    fatores_decisao: list[str] = Field(description="3 a 5 argumentos principais, cada um citando o parágrafo (§N)")
    riscos_alta: list[str] = Field(description="Riscos de alta para a inflação citados na ata")
    riscos_baixa: list[str] = Field(description="Riscos de baixa para a inflação citados na ata")
    orientacao_futura: str = Field(description="O que o Copom sinaliza sobre os próximos passos")
    paragrafos_citados: list[int]


SYSTEM_INSTRUCTIONS = """\
Você é um economista especializado em política monetária brasileira. Sua tarefa é ler uma ata do Copom \
e preencher uma análise estruturada, usando apenas o que está escrito na ata.

Regras:
- A decisão e a nova meta Selic estão na seção de decisão (geralmente perto do fim).
- Classifique o tom pela comunicação, não pela decisão: um corte pode ter tom duro se o Comitê enfatiza \
riscos inflacionários e cautela; uma manutenção pode ter tom brando se sinaliza cortes à frente.
- Cada fator da decisão deve citar o parágrafo de onde veio, no formato (§N).
- Se a ata não mencionar riscos de alta ou de baixa explicitamente, deixe a lista vazia; não invente.
- Atas antigas (antes de ~2008) não numeram parágrafos; nelas, use a numeração entre colchetes do texto.
"""

FEW_SHOT_ANSWER = AnaliseAta(
    decisao="corte",
    selic_nova=13.75,
    variacao_pontos_base=-25,
    unanime=True,
    votos_divergentes=[],
    tom="duro",
    justificativa_tom=(
        "Apesar do corte, o Comitê enfatiza riscos com assimetria altista, expectativas desancoradas que exigem "
        "restrição monetária maior e por mais tempo, e pede serenidade e cautela (§10, §13, §19)."
    ),
    fatores_decisao=[
        "Evidências acumuladas de transmissão da política monetária para a atividade (§15)",
        "Moderação gradual da atividade e desaceleração do crédito livre (§5, §6)",
        "Inflação cheia e subjacente em queda, abaixo do teto da meta mas ainda acima do centro (§9)",
        "Projeção de 3,2% no horizonte relevante, compatível com convergência gradual (§12, §18)",
    ],
    riscos_alta=[
        "Desancoragem prolongada das expectativas, com efeitos de segunda ordem de choques de petróleo e clima (§13)",
        "Inflação de serviços mais resiliente por hiato do produto mais positivo (§13)",
        "Taxa de câmbio persistentemente mais depreciada (§13)",
        "Estímulos à demanda agregada acima do produto potencial (§13)",
    ],
    riscos_baixa=[
        "Desaceleração da atividade doméstica mais acentuada que a projetada (§13)",
        "Desaceleração global mais forte por choques de comércio e petróleo (§13)",
        "Queda nos preços das commodities (§13)",
    ],
    orientacao_futura=(
        "A magnitude do ciclo de calibração será ajustada à evolução do cenário, mantendo a restrição adequada "
        "para a convergência da inflação à meta (§17, §19)."
    ),
    paragrafos_citados=[5, 6, 9, 10, 12, 13, 15, 17, 18, 19, 20],
)


def format_minutes(meeting: Meeting, chunks: Iterable[Chunk]) -> str:
    body = "\n\n".join(f"[§{c.paragraph}] ({c.section}) {c.text}" for c in chunks if c.paragraph > 0)
    return f'<ata reuniao="{meeting.number}" data="{meeting.date}">\n{body}\n</ata>'


def build_system(example_meeting: Meeting, example_chunks: Iterable[Chunk]) -> list[dict[str, Any]]:
    example = (
        "Exemplo resolvido:\n\n"
        f"{format_minutes(example_meeting, example_chunks)}\n\n"
        f"Análise esperada:\n{FEW_SHOT_ANSWER.model_dump_json(indent=1)}"
    )
    # O cache_control no último bloco faz instruções + exemplo (~3,5 mil tokens) ficarem em cache.
    return [
        {"type": "text", "text": SYSTEM_INSTRUCTIONS},
        {"type": "text", "text": example, "cache_control": {"type": "ephemeral"}},
    ]


def user_message(meeting: Meeting, chunks: Iterable[Chunk]) -> list[dict[str, Any]]:
    return [{"role": "user", "content": f"Analise esta ata:\n\n{format_minutes(meeting, chunks)}"}]


@dataclass
class Trace:
    meeting: int
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    latency_s: float
    cost_usd: float
    stop_reason: str


def estimate_cost(usage: Any, batch: bool = False) -> float:
    cost = (
        (getattr(usage, "input_tokens", 0) or 0) * PRICES["input"]
        + (getattr(usage, "output_tokens", 0) or 0) * PRICES["output"]
        + (getattr(usage, "cache_read_input_tokens", 0) or 0) * PRICES["cache_read"]
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * PRICES["cache_write"]
    ) / 1_000_000
    return round(cost * (0.5 if batch else 1.0), 6)


class ExtractionError(RuntimeError):
    pass


class Extractor:
    def __init__(self, client: Any, meetings: list[Meeting], chunks: list[Chunk], trace_path: Path | None = None):
        self.client = client
        self.by_number = {m.number: m for m in meetings}
        self.chunks_by_meeting: dict[int, list[Chunk]] = {}
        for c in chunks:
            self.chunks_by_meeting.setdefault(c.meeting, []).append(c)
        self.system = build_system(self.by_number[FEW_SHOT_MEETING], self.chunks_by_meeting[FEW_SHOT_MEETING])
        self.trace_path = trace_path

    def extract(self, number: int) -> AnaliseAta:
        meeting = self.by_number[number]
        started = time.perf_counter()
        response = self.client.beta.messages.parse(
            model=MODEL,
            max_tokens=16000,
            system=self.system,
            messages=user_message(meeting, self.chunks_by_meeting.get(number, [])),
            output_format=AnaliseAta,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        self.record(number, response, time.perf_counter() - started)
        if response.stop_reason == "refusal":
            raise ExtractionError(f"Reunião {number}: o modelo recusou a análise.")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise ExtractionError(f"Reunião {number}: resposta incompleta ({response.stop_reason}).")
        return response.parsed_output

    def batch_requests(self, numbers: Iterable[int]) -> list[dict[str, Any]]:
        """Requisições para a Batch API (50% mais barata). Ela não aceita `fallbacks` nem o helper `parse`,
        então o schema vai direto em `output_config.format`."""
        import anthropic

        schema = anthropic.transform_schema(AnaliseAta)
        return [
            {
                "custom_id": f"ata-{n}",
                "params": {
                    "model": MODEL,
                    "max_tokens": 16000,
                    "system": self.system,
                    "messages": user_message(self.by_number[n], self.chunks_by_meeting.get(n, [])),
                    "thinking": {"type": "adaptive"},
                    "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
                },
            }
            for n in numbers
        ]

    @staticmethod
    def parse_batch_result(result: Any) -> tuple[int, AnaliseAta | None, str | None]:
        number = int(result.custom_id.removeprefix("ata-"))
        if result.result.type != "succeeded":
            return number, None, result.result.type
        message = result.result.message
        if message.stop_reason != "end_turn":
            return number, None, message.stop_reason
        text = next(b.text for b in message.content if b.type == "text")
        return number, AnaliseAta.model_validate_json(text), None

    def record(self, number: int, response: Any, latency: float | None, batch: bool = False) -> None:
        """Registra tokens, custo estimado e latência de uma chamada em JSONL (observabilidade simples)."""
        if self.trace_path is None:
            return
        usage = response.usage
        trace = Trace(
            meeting=number,
            model=getattr(response, "model", MODEL),
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            latency_s=round(latency, 2) if latency is not None else -1,
            cost_usd=estimate_cost(usage, batch=batch),
            stop_reason=response.stop_reason,
        )
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        with self.trace_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(trace)) + "\n")


def load_analyses(path: Path) -> dict[int, AnaliseAta]:
    if not path.exists():
        return {}
    return {int(k): AnaliseAta.model_validate(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def save_analyses(path: Path, analyses: dict[int, AnaliseAta]) -> None:
    payload = {str(k): v.model_dump() for k, v in sorted(analyses.items())}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
