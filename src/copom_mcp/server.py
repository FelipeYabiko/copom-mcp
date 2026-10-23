"""Servidor MCP: expõe as atas do Copom e indicadores do Banco Central para assistentes de IA."""

from __future__ import annotations

import calendar
import importlib.util
import logging
import os
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Literal

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from .corpus import SGS_API, Chunk, Meeting, load_corpus, user_data_dir
from .extract import AnaliseAta, load_analyses
from .search import SearchEngine, SemanticIndex

log = logging.getLogger(__name__)
ATAS_PAGE = "https://www.bcb.gov.br/publicacoes/atascopom/cronologicos"

INSTRUCTIONS = """\
Este servidor dá acesso às atas do Copom (Comitê de Política Monetária do Banco Central do Brasil), \
de 1998 até hoje, e a indicadores econômicos oficiais.

- Para perguntas sobre o que o Copom pensou ou disse, use `buscar_atas` e responda com base nos trechos.
- Cite cada afirmação como (Ata da Nª reunião, §P, mês/ano) e informe o link da fonte.
- Para decisões de juros, use `listar_reunioes`; para números (Selic, IPCA, dólar), `consultar_indicador`.
- Parágrafos de atas anteriores a ~2008 não são numerados na ata original; ali o § é a ordem no texto.
"""

INDICATORS: dict[str, tuple[int, str, str]] = {
    "selic_meta": (432, "Meta da taxa Selic", "% a.a."),
    "ipca": (433, "IPCA (variação mensal)", "% no mês"),
    "ipca_12m": (13522, "IPCA acumulado em 12 meses", "%"),
    "igpm": (189, "IGP-M (variação mensal)", "% no mês"),
    "dolar": (1, "Dólar PTAX (venda)", "R$/US$"),
}
Indicator = Literal["selic_meta", "ipca", "ipca_12m", "igpm", "dolar"]
DateFilter = Annotated[str | None, Field(description="AAAA, AAAA-MM ou AAAA-MM-DD")]
DATE_FORMAT = re.compile(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?")
DATE_HELP = "Use AAAA, AAAA-MM ou AAAA-MM-DD (ex.: 2025, 2025-03 ou 2025-03-19)."


class Citation(BaseModel):
    reuniao: int
    data: str
    secao: str
    paragrafo: int
    texto: str
    fonte: str
    encontrado_por: list[str] = Field(description="Quais buscas encontraram o trecho (bm25, semântica)")


class SearchResult(BaseModel):
    consulta: str
    modo_de_busca: str
    resultados: list[Citation]


class MeetingSummary(BaseModel):
    reuniao: int
    data: str
    titulo: str
    selic_antes: float | None
    selic_depois: float | None
    decisao: Literal["alta", "corte", "manutenção"] | None = Field(description="None antes de mar/1999 (sem meta)")
    variacao_pontos_base: int | None
    fonte: str


class MeetingList(BaseModel):
    total: int
    reunioes: list[MeetingSummary]


class MinutesParagraph(BaseModel):
    numero: int
    texto: str


class MinutesSection(BaseModel):
    titulo: str
    paragrafos: list[MinutesParagraph]


class Minutes(BaseModel):
    reuniao: MeetingSummary
    secoes: list[MinutesSection]


class SeriesPoint(BaseModel):
    data: str
    valor: float


class Series(BaseModel):
    indicador: str
    nome: str
    unidade: str
    periodicidade: str
    pontos: list[SeriesPoint]
    fonte: str


def create_server(
    meetings: list[Meeting] | None = None,
    chunks: list[Chunk] | None = None,
    http: httpx.Client | None = None,
    semantic: bool | None = None,
    analyses: dict[int, AnaliseAta] | None = None,
) -> MCPServer:
    if meetings is None or chunks is None:
        meetings, chunks = load_corpus()
    if analyses is None:
        analyses = load_analyses(Path(__file__).parent / "data" / "analises.json")
    by_number = {m.number: m for m in meetings}
    engine = SearchEngine(chunks)
    if semantic is not False and _semantic_available():
        engine.load_semantic(lambda: SemanticIndex(chunks, user_data_dir()))
    http = http or httpx.Client(timeout=30)
    log.info("Corpus: %d reuniões, %d parágrafos. Busca: %s.", len(meetings), len(chunks), engine.mode)

    server = MCPServer(name="copom-mcp", title="Atas do Copom", instructions=INSTRUCTIONS, version="0.1.0")
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)

    def summary(m: Meeting) -> MeetingSummary:
        return MeetingSummary(
            reuniao=m.number,
            data=m.date,
            titulo=m.title,
            selic_antes=m.selic_before,
            selic_depois=m.selic_after,
            decisao=m.decision,  # type: ignore[arg-type]
            variacao_pontos_base=m.change_bp,
            fonte=m.pdf_url or ATAS_PAGE,
        )

    def get_meeting(number: int) -> Meeting:
        if number not in by_number:
            first, last = min(by_number), max(by_number)
            raise ToolError(f"Reunião {number} não encontrada. Há atas da {first}ª à {last}ª reunião.")
        return by_number[number]

    @server.tool(title="Buscar nas atas do Copom", annotations=read_only)
    def buscar_atas(
        consulta: Annotated[str, Field(description="Tema ou pergunta, em português. Ex.: 'inflação de serviços'")],
        limite: Annotated[int, Field(ge=1, le=25)] = 8,
        desde: DateFilter = None,
        ate: DateFilter = None,
        reuniao: Annotated[int | None, Field(description="Restringe a uma reunião")] = None,
    ) -> SearchResult:
        """Busca parágrafos das atas do Copom sobre um tema. Devolve trechos com reunião, data, seção e
        parágrafo para citar. Combine com `desde`/`ate` para comparar períodos.

        As atas usam linguagem técnica: se a primeira busca não trouxer o tema, tente o vocabulário do
        Copom (ex.: "choque de tarifas" em vez de "guerra comercial", "desancoragem das expectativas" em
        vez de "inflação fora do controle") ou faça várias buscas curtas."""
        if reuniao is not None:
            get_meeting(reuniao)
        since, until = _period(desde, ate)
        hits = engine.search(consulta, limite, _iso(since), _iso(until), reuniao)
        return SearchResult(
            consulta=consulta,
            modo_de_busca=engine.mode,
            resultados=[
                Citation(
                    reuniao=h.chunk.meeting,
                    data=h.chunk.date,
                    secao=h.chunk.section,
                    paragrafo=h.chunk.paragraph,
                    texto=h.chunk.text,
                    fonte=by_number[h.chunk.meeting].pdf_url or ATAS_PAGE,
                    encontrado_por=list(h.matched_by),
                )
                for h in hits
            ],
        )

    @server.tool(title="Ler uma ata do Copom", annotations=read_only)
    def ler_ata(
        reuniao: Annotated[int, Field(description="Número da reunião. Ex.: 281")],
        secao: Annotated[str | None, Field(description="Trecho do título da seção, para ler só ela")] = None,
    ) -> Minutes:
        """Devolve o texto de uma ata, organizado por seção e parágrafo, com a decisão de juros da reunião."""
        meeting = get_meeting(reuniao)
        paragraphs = [c for c in chunks if c.meeting == reuniao and c.paragraph > 0]
        if secao is not None:
            wanted = _fold(secao.strip())
            selected = [c for c in paragraphs if wanted in _fold(c.section)]
            if not selected:
                titles = "; ".join(dict.fromkeys(c.section for c in paragraphs))
                raise ToolError(f"A ata da {reuniao}ª reunião não tem seção com '{secao}'. Seções: {titles}.")
            paragraphs = selected
        sections: dict[str, list[MinutesParagraph]] = {}
        for c in paragraphs:
            sections.setdefault(c.section, []).append(MinutesParagraph(numero=c.paragraph, texto=c.text))
        return Minutes(
            reuniao=summary(meeting),
            secoes=[MinutesSection(titulo=t, paragrafos=p) for t, p in sections.items()],
        )

    @server.tool(title="Listar reuniões e decisões", annotations=read_only)
    def listar_reunioes(
        desde: DateFilter = None,
        ate: DateFilter = None,
        decisao: Literal["alta", "corte", "manutenção"] | None = None,
    ) -> MeetingList:
        """Lista reuniões do Copom com a meta Selic antes e depois e a decisão (alta, corte ou manutenção)."""
        start, end = _period(desde, ate)
        since, until = _iso(start), _iso(end)
        selected = [
            summary(m)
            for m in meetings
            if (since is None or m.date >= since)
            and (until is None or m.date <= until)
            and (decisao is None or m.decision == decisao)
        ]
        return MeetingList(total=len(selected), reunioes=selected)

    @server.tool(
        title="Consultar indicador do Banco Central",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    )
    def consultar_indicador(
        indicador: Indicator,
        inicio: Annotated[str, Field(description="AAAA, AAAA-MM ou AAAA-MM-DD")],
        fim: Annotated[str | None, Field(description="AAAA, AAAA-MM ou AAAA-MM-DD; padrão: hoje")] = None,
    ) -> Series:
        """Série oficial do Banco Central (SGS) entre duas datas. Séries diárias longas (mais de 400
        pontos) voltam com o último valor de cada mês, para caber na conversa."""
        code, name, unit = INDICATORS[indicador]
        start = _parse_date(inicio, "inicio")
        end = _parse_date(fim, "fim", end=True) if fim else date.today()
        if start > end:
            raise ToolError(f"`inicio` ({inicio}) é depois de `fim` ({fim or 'hoje'}).")
        try:
            points = _fetch_series(http, code, start, end)
        except httpx.TimeoutException as exc:
            raise ToolError(
                "A API do Banco Central (SGS) não respondeu a tempo. Tente de novo em instantes ou com um "
                "período menor."
            ) from exc
        except httpx.TransportError as exc:
            raise ToolError(
                f"Não foi possível conectar à API do Banco Central (SGS): {exc}. Tente de novo em instantes."
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                detail = "limitou as consultas (HTTP 429). Espere alguns segundos e tente de novo."
            elif status >= 500:
                detail = f"está com problemas (HTTP {status}). Tente de novo em alguns minutos."
            else:
                detail = f"recusou a consulta (HTTP {status})."
            raise ToolError(f"A API do Banco Central (SGS) {detail}") from exc
        except (ValueError, KeyError, TypeError) as exc:  # JSON inválido ou fora do formato da série
            raise ToolError(
                f"A API do Banco Central (SGS) devolveu uma resposta inesperada para a série {code}. "
                "Tente de novo em instantes."
            ) from exc
        if not points:
            raise ToolError(
                f"Não há dados de {name} entre {start} e {end}. O período pode ser anterior ao início da série "
                "ou curto demais: séries diárias, como o dólar, não têm fins de semana e feriados, e as mensais "
                "(IPCA, IGP-M) saem cerca de um mês depois. Amplie o período."
            )
        frequency = "original"
        if len(points) > 400:
            monthly: dict[str, SeriesPoint] = {}
            for p in points:
                monthly[p.data[:7]] = p
            points, frequency = list(monthly.values()), "mensal (último valor do mês)"
        return Series(
            indicador=indicador,
            nome=name,
            unidade=unit,
            periodicidade=frequency,
            pontos=points,
            fonte=f"https://www3.bcb.gov.br/sgspub/ (série {code})",
        )

    @server.tool(title="Análise estruturada de uma reunião", annotations=read_only)
    def analise_reuniao(reuniao: Annotated[int, Field(description="Número da reunião")]) -> AnaliseAta:
        """Análise gerada por IA (Gemini 3.5 Flash-Lite) de uma ata: decisão, tom (duro/neutro/brando),
        fatores, riscos e orientação futura, com os parágrafos citados. Tom e fatores são interpretação do
        modelo: para conferir, leia a ata com `ler_ata`. Para os números oficiais (Selic antes e depois,
        variação), use `listar_reunioes`."""
        if get_meeting(reuniao).decision is None:
            raise ToolError(
                f"A {reuniao}ª reunião é anterior à meta Selic (mar/1999) e não tem análise estruturada. "
                "Use `ler_ata` e analise o texto diretamente."
            )
        if reuniao not in analyses:
            raise ToolError(f"Ainda não há análise da {reuniao}ª reunião. Use `ler_ata` e analise o texto diretamente.")
        return analyses[reuniao]

    @server.resource("copom://reunioes", name="reunioes", mime_type="application/json")
    def resource_meetings() -> str:
        """Todas as reuniões do Copom com as decisões de juros."""
        return MeetingList(total=len(meetings), reunioes=[summary(m) for m in meetings]).model_dump_json(indent=1)

    @server.resource("copom://atas/{reuniao}", name="ata", mime_type="text/markdown")
    def resource_minutes(reuniao: str) -> str:
        """Texto completo de uma ata em Markdown."""
        if not reuniao.isdigit() or int(reuniao) not in by_number:
            raise ResourceNotFoundError(f"Não há ata da reunião {reuniao}.")
        minutes = ler_ata(int(reuniao))
        meeting = minutes.reuniao
        lines = [f"# {meeting.titulo} ({meeting.data})", "", f"Fonte: {meeting.fonte}", ""]
        for section in minutes.secoes:
            lines += [f"## {section.titulo}", ""]
            lines += [f"{p.numero}. {p.texto}\n" for p in section.paragrafos]
        return "\n".join(lines)

    @server.prompt(title="Analisar uma reunião do Copom")
    def analisar_reuniao(reuniao: str) -> str:
        """Roteiro para analisar a decisão e o tom de uma reunião."""
        return (
            f"Analise a {reuniao}ª reunião do Copom. Use `ler_ata` para ler a ata e `listar_reunioes` para "
            "comparar com a reunião anterior. Explique: (1) a decisão de juros, (2) os principais argumentos, "
            "(3) os riscos citados e (4) se o tom ficou mais duro, mais brando ou igual. Cite os parágrafos."
        )

    @server.prompt(title="Comparar duas reuniões do Copom")
    def comparar_reunioes(reuniao_a: str, reuniao_b: str) -> str:
        """Roteiro para comparar a comunicação de duas reuniões."""
        return (
            f"Compare a {reuniao_a}ª e a {reuniao_b}ª reuniões do Copom. Leia as duas atas com `ler_ata`, aponte "
            "o que mudou no diagnóstico, nos riscos e na orientação futura, e cite os parágrafos de cada uma."
        )

    return server


def _parse_date(value: str, param: str, end: bool = False) -> date:
    """`2025`, `2025-03` ou `2025-03-19`. Ano e mês viram o primeiro dia do período, ou o último se `end`."""
    match = DATE_FORMAT.fullmatch(value.strip())
    if match:
        year, month, day = match.groups()
        try:
            if day:
                return date(int(year), int(month), int(day))
            if month:
                last = calendar.monthrange(int(year), int(month))[1]
                return date(int(year), int(month), last if end else 1)
            return date(int(year), 12, 31) if end else date(int(year), 1, 1)
        except ValueError:  # mês 13, 30 de fevereiro
            pass
    raise ToolError(f"Data inválida em `{param}`: '{value}'. {DATE_HELP}")


def _period(desde: str | None, ate: str | None) -> tuple[date | None, date | None]:
    since = _parse_date(desde, "desde") if desde else None
    until = _parse_date(ate, "ate", end=True) if ate else None
    if since and until and since > until:
        raise ToolError(f"`desde` ({desde}) é depois de `ate` ({ate}).")
    return since, until


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _fold(text: str) -> str:
    """Minúsculas e sem acentos: "decisao" encontra a seção "Decisão de política monetária"."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def _fetch_series(http: httpx.Client, code: int, start: date, end: date) -> list[SeriesPoint]:
    points: list[SeriesPoint] = []
    current = start
    while current <= end:  # séries diárias: no máximo 10 anos por consulta
        window_end = min(current + timedelta(days=365 * 5), end)
        response = http.get(
            SGS_API.format(code=code),
            params={
                "formato": "json",
                "dataInicial": current.strftime("%d/%m/%Y"),
                "dataFinal": window_end.strftime("%d/%m/%Y"),
            },
        )
        if response.status_code != 404:  # 404 = sem dados no período
            response.raise_for_status()
            for row in response.json():
                d, m, y = row["data"].split("/")
                points.append(SeriesPoint(data=f"{y}-{m}-{d}", valor=float(row["valor"])))
        current = window_end + timedelta(days=1)
    return points


def _semantic_available() -> bool:
    if os.environ.get("COPOM_MCP_SEMANTIC", "1") == "0":
        return False
    if importlib.util.find_spec("fastembed") is None:
        log.info("fastembed não instalado: usando só BM25. Para busca híbrida: pip install 'copom-mcp[semantic]'.")
        return False
    return True
