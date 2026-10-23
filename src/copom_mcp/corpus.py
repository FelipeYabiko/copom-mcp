"""Download das atas do Copom e da meta Selic, e leitura/gravação do corpus local."""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from importlib import resources
from pathlib import Path

import httpx

from .parsing import Paragraph, html_to_lines, pdf_to_lines, segment

COPOM_API = "https://www.bcb.gov.br/api/servico/sitebcb/copom"
SGS_API = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados"
SELIC_TARGET = 432
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Meeting:
    number: int
    date: str  # último dia da reunião (AAAA-MM-DD)
    published: str
    title: str
    pdf_url: str | None = None
    selic_before: float | None = None
    selic_after: float | None = None

    @property
    def decision(self) -> str | None:
        if self.selic_before is None or self.selic_after is None:
            return None
        delta = round(self.selic_after - self.selic_before, 2)
        return "manutenção" if delta == 0 else ("alta" if delta > 0 else "corte")

    @property
    def change_bp(self) -> int | None:
        if self.selic_before is None or self.selic_after is None:
            return None
        return round((self.selic_after - self.selic_before) * 100)


@dataclass(frozen=True)
class Chunk:
    meeting: int
    date: str
    section: str
    paragraph: int
    text: str

    @property
    def id(self) -> str:
        return f"{self.meeting}-{self.paragraph}"


class BCBClient:
    def __init__(self, client: httpx.Client | None = None, retries: int = 3) -> None:
        self._client = client or httpx.Client(timeout=60, follow_redirects=True)
        self._retries = retries

    def _get(self, url: str, **params: str | int) -> httpx.Response:
        for attempt in range(self._retries + 1):
            try:
                response = self._client.get(url, params=params)
                if response.status_code < 500:
                    response.raise_for_status()
                    return response
            except httpx.TransportError:
                if attempt == self._retries:
                    raise
            if attempt < self._retries:
                time.sleep(2**attempt)
        response.raise_for_status()
        return response

    def list_meetings(self) -> list[Meeting]:
        payload = self._get(f"{COPOM_API}/atas", quantidade=1000).json()["conteudo"]
        return sorted(
            (Meeting(m["nroReuniao"], m["dataReferencia"], m["dataPublicacao"], m["titulo"]) for m in payload),
            key=lambda m: m.number,
        )

    def fetch_minutes(self, number: int) -> tuple[str | None, list[Paragraph]]:
        """Baixa uma ata e devolve (url do PDF, parágrafos). Usa o PDF quando não há HTML."""
        content = self._get(f"{COPOM_API}/atas_detalhes", nro_reuniao=number).json()["conteudo"]
        if not content:
            return None, []
        detail = content[0]
        pdf_url = detail.get("urlPdfAta")
        if (detail.get("textoAta") or "").strip():
            lines, toc = html_to_lines(detail["textoAta"])
        elif pdf_url:
            lines, toc = pdf_to_lines(self._get(pdf_url).content), set()
        else:
            return pdf_url, []
        return pdf_url, segment(lines, toc)

    def selic_target(self, start: date, end: date) -> dict[str, float]:
        """Meta Selic diária, em janelas de 5 anos (a API limita séries diárias a 10 anos)."""
        values: dict[str, float] = {}
        current = start
        while current <= end:
            window_end = min(current + timedelta(days=365 * 5), end)
            response = self._get(
                SGS_API.format(code=SELIC_TARGET),
                formato="json",
                dataInicial=current.strftime("%d/%m/%Y"),
                dataFinal=window_end.strftime("%d/%m/%Y"),
            )
            for row in response.json():
                d, m, y = row["data"].split("/")
                values[f"{y}-{m}-{d}"] = float(row["valor"])
            current = window_end + timedelta(days=1)
        return values


def attach_decisions(meetings: Iterable[Meeting], selic: dict[str, float]) -> list[Meeting]:
    """A decisão vale a partir do dia útil seguinte à reunião. Com feriado no meio (Corpus Christi, 7 de
    setembro, Semana Santa), a nova meta só aparece na série dias depois. A busca vai até 5 dias à frente, o
    pior caso (reunião na quarta, feriados na quinta e na sexta, fim de semana). Mais que isso não é decisão
    da reunião: na época do viés (1999-2003), a meta podia mudar entre reuniões, como 7 dias após a 45ª."""
    result = []
    for m in meetings:
        day = date.fromisoformat(m.date)
        before = selic.get(m.date)
        following = [selic.get((day + timedelta(days=k)).isoformat()) for k in range(1, 6)]
        after = next((v for v in following if v is not None and v != before), following[0])
        result.append(Meeting(**{**asdict(m), "selic_before": before, "selic_after": after}))
    return result


# --- Armazenamento -------------------------------------------------------------------------------


def user_data_dir() -> Path:
    base = os.environ.get("COPOM_MCP_DATA") or os.path.join(
        os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share", "copom-mcp"
    )
    return Path(base)


def save_corpus(directory: Path, meetings: list[Meeting], chunks: list[Chunk]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "meetings.json").write_text(
        json.dumps([asdict(m) for m in meetings], ensure_ascii=False, indent=1), encoding="utf-8"
    )
    # mtime=0 deixa o arquivo idêntico entre builds iguais (diffs limpos no git).
    with open(directory / "chunks.jsonl.gz", "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        for chunk in chunks:
            gz.write((json.dumps(asdict(chunk), ensure_ascii=False) + "\n").encode())


def load_corpus(directory: Path | None = None) -> tuple[list[Meeting], list[Chunk]]:
    """Carrega o corpus do usuário (se `copom-mcp atualizar` já rodou) ou o que vem no pacote."""
    if directory is None:
        user = user_data_dir()
        directory = user if (user / "meetings.json").exists() else None
    if directory is None:
        base = resources.files("copom_mcp") / "data"
        meetings_raw = (base / "meetings.json").read_text(encoding="utf-8")
        chunks_raw = gzip.decompress((base / "chunks.jsonl.gz").read_bytes()).decode()
    else:
        meetings_raw = (directory / "meetings.json").read_text(encoding="utf-8")
        chunks_raw = gzip.decompress((directory / "chunks.jsonl.gz").read_bytes()).decode()
    meetings = [Meeting(**m) for m in json.loads(meetings_raw)]
    chunks = [Chunk(**json.loads(line)) for line in chunks_raw.splitlines() if line]
    return meetings, chunks


def build_corpus(
    client: BCBClient,
    existing: tuple[list[Meeting], list[Chunk]] | None = None,
    progress: Callable[[str], None] = lambda _: None,
) -> tuple[list[Meeting], list[Chunk]]:
    """Monta o corpus. Com `existing`, só baixa atas novas (atualização incremental)."""
    known_meetings, known_chunks = existing or ([], [])
    have = {m.number: m for m in known_meetings}
    chunks = list(known_chunks)
    meetings: list[Meeting] = []

    for meeting in client.list_meetings():
        if meeting.number in have:
            meetings.append(have[meeting.number])
            continue
        progress(f"Baixando ata da {meeting.number}ª reunião ({meeting.date})")
        pdf_url, paragraphs = client.fetch_minutes(meeting.number)
        meetings.append(Meeting(**{**asdict(meeting), "pdf_url": pdf_url}))
        chunks += [Chunk(meeting.number, meeting.date, p.section, p.number, p.text) for p in paragraphs]

    progress("Baixando a meta Selic")
    selic = client.selic_target(date(1999, 3, 5), date.today())
    meetings = attach_decisions(meetings, selic)
    chunks.sort(key=lambda c: (c.meeting, c.paragraph))
    return meetings, chunks
