"""Extração com provedores compatíveis com a API da OpenAI (Groq, Gemini, OpenRouter), para testar sem custo.

O caminho principal continua sendo o Claude (`extract.py`). Aqui o mesmo schema, as mesmas instruções e o
mesmo exemplo few-shot vão para o endpoint `/chat/completions`, que esses provedores implementam. Diferenças:
- Sem prompt caching nem Batch API: cada chamada manda o prompt inteiro.
- Saída em modo JSON com o schema no prompt; a resposta é validada pelo Pydantic e, se vier errada, o
  modelo recebe o erro e tem mais uma tentativa.
- Planos gratuitos limitam tokens por minuto e por dia: o 429 é respeitado (`retry-after`) e, quando a cota
  do dia acaba, a execução para em vez de insistir.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from .corpus import Chunk, Meeting
from .extract import (
    FEW_SHOT_ANSWER,
    FEW_SHOT_MEETING,
    SYSTEM_INSTRUCTIONS,
    AnaliseAta,
    ExtractionError,
    Trace,
    format_minutes,
    user_message,
)


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: str
    default_model: str | None
    # Aceita `response_format` do tipo json_schema; senão usa json_object (o schema vai no prompt de todo jeito).
    json_schema: bool
    # O exemplo few-shot soma ~4 mil tokens. No Groq gratuito (~8 mil tokens/min) ele não cabe junto da ata.
    few_shot: bool


PROVIDERS = {
    "groq": Provider(
        "groq",
        "https://api.groq.com/openai/v1",
        "GROQ_API_KEY",
        "openai/gpt-oss-120b",
        json_schema=False,
        few_shot=False,
    ),
    "gemini": Provider(
        "gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "GEMINI_API_KEY",
        "gemini-3.5-flash-lite",
        json_schema=True,
        few_shot=True,
    ),
    # Os modelos gratuitos do OpenRouter (sufixo ":free") mudam com frequência, então o modelo é obrigatório.
    "openrouter": Provider(
        "openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", None, json_schema=True, few_shot=True
    ),
}

MAX_RETRIES = 3
MAX_WAIT_S = 120.0  # um retry-after maior que isso é cota diária, não por minuto
_TRY_AGAIN = re.compile(r"try again in (?:(\d+)h)?(?:(\d+)m)?([\d.]+)s", re.IGNORECASE)
_DAILY = ("per day", "perday", "per-day", "(tpd)", "(rpd)", "daily")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


class ProviderUnavailable(RuntimeError):
    """Erro que vale para todas as atas (cota do dia, chave inválida, modelo inexistente): a execução para."""


class RequestTooLarge(ExtractionError):
    """A ata não cabe no limite por requisição do provedor. Esperado em atas longas: pula só esta."""


def retry_after(resp: httpx.Response) -> float | None:
    """Segundos de espera de um 429: cabeçalho `retry-after` ou "try again in 1m2.5s" no corpo (Groq)."""
    header = resp.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    m = _TRY_AGAIN.search(resp.text)
    if m:
        return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3))
    return None


def is_daily_quota(resp: httpx.Response) -> bool:
    text = resp.text.lower()
    return any(marker in text for marker in _DAILY)


class CompatExtractor:
    def __init__(
        self,
        provider: Provider,
        api_key: str,
        meetings: list[Meeting],
        chunks: list[Chunk],
        model: str | None = None,
        few_shot: bool | None = None,
        trace_path: Path | None = None,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not (model or provider.default_model):
            raise ValueError(f"O provedor {provider.name} precisa de --modelo.")
        self.provider = provider
        self.model = model or provider.default_model
        self.api_key = api_key
        self.by_number = {m.number: m for m in meetings}
        self.chunks_by_meeting: dict[int, list[Chunk]] = {}
        for c in chunks:
            self.chunks_by_meeting.setdefault(c.meeting, []).append(c)
        use_example = provider.few_shot if few_shot is None else few_shot
        self.system = self.build_system(use_example)
        self.trace_path = trace_path
        self.http = http or httpx.Client(timeout=180.0)
        self.sleep = sleep

    @property
    def label(self) -> str:
        return f"{self.provider.name}:{self.model}"

    def build_system(self, few_shot: bool) -> str:
        parts = [SYSTEM_INSTRUCTIONS]
        if few_shot:
            example = self.chunks_by_meeting.get(FEW_SHOT_MEETING, [])
            parts.append(
                "Exemplo resolvido:\n\n"
                f"{format_minutes(self.by_number[FEW_SHOT_MEETING], example)}\n\n"
                f"Análise esperada:\n{FEW_SHOT_ANSWER.model_dump_json(indent=1)}"
            )
        schema = json.dumps(AnaliseAta.model_json_schema(), ensure_ascii=False)
        parts.append(f"Responda apenas com um objeto JSON válido que siga este JSON Schema:\n{schema}")
        return "\n\n".join(parts)

    def payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if self.provider.json_schema:
            schema = AnaliseAta.model_json_schema()
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "analise_ata", "schema": schema, "strict": False},
            }
        else:
            payload["response_format"] = {"type": "json_object"}
        if "gpt-oss" in self.model:
            # Modelo de raciocínio: com o esforço padrão ele pode gastar a cota de tokens pensando.
            payload["reasoning_effort"] = "low"
        return payload

    def extract(self, number: int) -> AnaliseAta:
        meeting = self.by_number[number]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system},
            *user_message(meeting, self.chunks_by_meeting.get(number, [])),
        ]
        for attempt in range(2):
            text, finish = self.complete(number, messages)
            if finish == "length":
                raise ExtractionError(f"Reunião {number}: resposta incompleta (length).")
            try:
                return AnaliseAta.model_validate_json(_FENCE.sub("", text.strip()))
            except ValidationError as exc:
                if attempt:
                    raise ExtractionError(f"Reunião {number}: resposta fora do schema duas vezes.") from exc
                errors = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'json'}: {e['msg']}" for e in exc.errors())
                messages += [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": f"A resposta não seguiu o schema ({errors}). Envie só o JSON corrigido.",
                    },
                ]
        raise AssertionError("inalcançável")

    def complete(self, number: int, messages: list[dict[str, Any]]) -> tuple[str, str | None]:
        """Uma chamada a /chat/completions, com novas tentativas para 429 por minuto, 5xx e falhas de rede."""
        for attempt in range(MAX_RETRIES + 1):
            last = attempt == MAX_RETRIES
            started = time.perf_counter()
            try:
                resp = self.http.post(
                    f"{self.provider.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=self.payload(messages),
                )
            except httpx.TransportError as exc:
                if last:
                    raise ExtractionError(f"Reunião {number}: erro de rede ({exc}).") from exc
                self.sleep(2**attempt)
                continue
            latency = time.perf_counter() - started

            if resp.status_code == 429:
                wait = retry_after(resp)
                if is_daily_quota(resp) or (wait or 0) > MAX_WAIT_S:
                    raise ProviderUnavailable(f"Cota do {self.provider.name} esgotada: {resp.text[:300]}")
                if last:
                    raise ExtractionError(f"Reunião {number}: limite de requisições do {self.provider.name}.")
                self.sleep(wait if wait is not None else 5 * 2**attempt)
                continue
            if resp.status_code == 413:
                raise RequestTooLarge(
                    f"Reunião {number}: a ata passa do limite de tokens por requisição do {self.provider.name}. "
                    "Tente outro modelo ou provedor (o Gemini aceita atas longas)."
                )
            if resp.status_code >= 500 and not last:
                self.sleep(2**attempt)
                continue
            if resp.status_code == 400 and (failed := _failed_generation(resp)) is not None:
                # Groq em modo JSON recusa uma geração inválida; o texto volta para a nova tentativa corretiva.
                return failed, "json_validate_failed"
            if resp.status_code in (401, 402, 403, 404) or (resp.status_code == 400 and _invalid_key(resp)):
                raise ProviderUnavailable(f"{self.provider.name} HTTP {resp.status_code}: {resp.text[:300]}")
            if resp.status_code >= 400:
                raise ExtractionError(
                    f"Reunião {number}: {self.provider.name} HTTP {resp.status_code}: {resp.text[:300]}"
                )

            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            self.record(number, data.get("usage") or {}, latency, choice.get("finish_reason"))
            return (choice.get("message") or {}).get("content") or "", choice.get("finish_reason")
        raise AssertionError("inalcançável")

    def record(self, number: int, usage: dict[str, Any], latency: float, finish: str | None) -> None:
        """Mesmo formato de trace do Claude. Custo 0: a ideia aqui é o plano gratuito."""
        if self.trace_path is None:
            return
        trace = Trace(
            meeting=number,
            model=self.label,
            input_tokens=usage.get("prompt_tokens") or 0,
            output_tokens=usage.get("completion_tokens") or 0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            latency_s=round(latency, 2),
            cost_usd=0.0,
            stop_reason=finish or "",
        )
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        with self.trace_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(trace)) + "\n")


def _error(resp: httpx.Response) -> dict[str, Any]:
    """O objeto `error` da resposta. O Gemini manda o corpo como lista: `[{"error": {...}}]`."""
    try:
        body = resp.json()
    except ValueError:
        return {}
    if isinstance(body, list):
        body = body[0] if body else {}
    err = body.get("error") if isinstance(body, dict) else None
    return err if isinstance(err, dict) else {}


def _failed_generation(resp: httpx.Response) -> str | None:
    err = _error(resp)
    if err.get("code") == "json_validate_failed":
        return str(err.get("failed_generation") or "")
    return None


def _invalid_key(resp: httpx.Response) -> bool:
    # O Gemini responde chave inválida com 400 ("Please pass a valid API key"), não 401.
    return "api key" in str(_error(resp).get("message", "")).lower()


def most_recent(numbers: Iterable[int], limit: int | None) -> list[int]:
    """As `limit` reuniões mais recentes (ou todas), em ordem crescente."""
    ordered = sorted(numbers)
    return ordered[-limit:] if limit else ordered
