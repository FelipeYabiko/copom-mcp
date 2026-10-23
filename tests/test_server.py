"""Testa o servidor pelo protocolo MCP de verdade, com um cliente conectado em processo."""

import json
import sys

import httpx
import pytest
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS

from copom_mcp.extract import FEW_SHOT_ANSWER
from copom_mcp.server import create_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def fake_sgs(request: httpx.Request) -> httpx.Response:
    assert "bcdata.sgs.13522" in str(request.url)
    return httpx.Response(200, json=[{"data": "01/08/2026", "valor": "4.22"}, {"data": "01/09/2026", "valor": "4.58"}])


@pytest.fixture
def server(meetings, chunks):
    http = httpx.Client(transport=httpx.MockTransport(fake_sgs))
    analyses = {281: FEW_SHOT_ANSWER}
    return create_server(meetings, chunks, http=http, semantic=False, analyses=analyses)


async def test_lists_tools_with_output_schemas(server):
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"buscar_atas", "ler_ata", "listar_reunioes", "consultar_indicador", "analise_reuniao"}
    assert all(t.output_schema for t in tools.values())
    assert tools["buscar_atas"].annotations.read_only_hint is True


async def test_search_returns_citations(server):
    async with Client(server) as client:
        result = await client.call_tool("buscar_atas", {"consulta": "petróleo", "limite": 2})
    first = result.structured_content["resultados"][0]
    assert (first["reuniao"], first["paragrafo"], first["fonte"]) == (279, 2, "https://bcb/279.pdf")


async def test_read_minutes_by_section(server):
    async with Client(server) as client:
        result = await client.call_tool("ler_ata", {"reuniao": 281, "secao": "decisão"})
    data = result.structured_content
    assert data["reuniao"]["decisao"] == "manutenção"
    assert [s["titulo"] for s in data["secoes"]] == ["C) Decisão"]


async def test_unknown_meeting_explains_the_valid_range(server):
    async with Client(server) as client:
        result = await client.call_tool("ler_ata", {"reuniao": 999})
    assert result.is_error
    assert "da 30ª à 281ª" in result.content[0].text


async def test_structured_analysis(server):
    async with Client(server) as client:
        found = await client.call_tool("analise_reuniao", {"reuniao": 281})
        missing = await client.call_tool("analise_reuniao", {"reuniao": 280})
    assert found.structured_content["tom"] == "duro"
    assert missing.is_error and "ler_ata" in missing.content[0].text


async def test_analysis_of_a_meeting_before_the_selic_target(server):
    async with Client(server) as client:
        result = await client.call_tool("analise_reuniao", {"reuniao": 30})
    assert result.is_error and "anterior à meta Selic" in result.content[0].text


async def test_list_meetings_with_filters(server):
    async with Client(server) as client:
        result = await client.call_tool("listar_reunioes", {"desde": "2026", "decisao": "corte"})
    data = result.structured_content
    assert [(m["reuniao"], m["variacao_pontos_base"]) for m in data["reunioes"]] == [(279, -25), (280, -25)]


async def test_indicator_uses_sgs(server):
    async with Client(server) as client:
        result = await client.call_tool("consultar_indicador", {"indicador": "ipca_12m", "inicio": "2026-08-01"})
    assert [p["valor"] for p in result.structured_content["pontos"]] == [4.22, 4.58]


async def test_search_unknown_meeting_explains_the_valid_range(server):
    async with Client(server) as client:
        result = await client.call_tool("buscar_atas", {"consulta": "inflação", "reuniao": 999})
    assert result.is_error and "da 30ª à 281ª" in result.content[0].text


async def test_section_filter_ignores_accents_and_lists_sections(server):
    async with Client(server) as client:
        found = await client.call_tool("ler_ata", {"reuniao": 281, "secao": "decisao"})
        missing = await client.call_tool("ler_ata", {"reuniao": 281, "secao": "riscos"})
    assert [s["titulo"] for s in found.structured_content["secoes"]] == ["C) Decisão"]
    assert missing.is_error and "Seções: A) Conjuntura; C) Decisão." in missing.content[0].text


@pytest.mark.parametrize(
    ("tool", "args", "message"),
    [
        ("buscar_atas", {"consulta": "inflação", "desde": "01/01/2025"}, "`desde`: '01/01/2025'"),
        ("listar_reunioes", {"desde": "março de 2026"}, "`desde`: 'março de 2026'"),
        ("listar_reunioes", {"ate": "2026-02-30"}, "`ate`: '2026-02-30'"),
        ("listar_reunioes", {"desde": "2026", "ate": "2025"}, "`desde` (2026) é depois de `ate` (2025)"),
        ("consultar_indicador", {"indicador": "ipca_12m", "inicio": "2026-13"}, "`inicio`: '2026-13'"),
        ("consultar_indicador", {"indicador": "ipca_12m", "inicio": "2026", "fim": "2025"}, "é depois de `fim`"),
    ],
)
async def test_invalid_dates_explain_the_format(server, tool, args, message):
    async with Client(server) as client:
        result = await client.call_tool(tool, args)
    assert result.is_error and message in result.content[0].text


async def test_month_and_year_filters(server):
    async with Client(server) as client:
        meetings = await client.call_tool("listar_reunioes", {"desde": "2026-08", "ate": "2026-08"})
        series = await client.call_tool("consultar_indicador", {"indicador": "ipca_12m", "inicio": "2026-08"})
    assert [m["reuniao"] for m in meetings.structured_content["reunioes"]] == [280]
    assert len(series.structured_content["pontos"]) == 2


def failing_sgs(kind: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if kind == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if kind == "conexão":
            raise httpx.ConnectError("connection refused", request=request)
        if kind == "html":
            return httpx.Response(200, text="<html>manutenção</html>")
        if kind == "vazio":
            return httpx.Response(404)
        return httpx.Response(int(kind))

    return handler


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("500", "está com problemas (HTTP 500)"),
        ("429", "limitou as consultas"),
        ("timeout", "não respondeu a tempo"),
        ("conexão", "Não foi possível conectar"),
        ("html", "resposta inesperada"),
        ("vazio", "Não há dados de IPCA acumulado em 12 meses"),
    ],
)
async def test_sgs_failures_reach_the_client(meetings, chunks, kind, message):
    http = httpx.Client(transport=httpx.MockTransport(failing_sgs(kind)))
    server = create_server(meetings, chunks, http=http, semantic=False, analyses={})
    async with Client(server) as client:
        result = await client.call_tool("consultar_indicador", {"indicador": "ipca_12m", "inicio": "2026-01-01"})
    assert result.is_error and message in result.content[0].text


async def test_resources_and_prompts(server):
    async with Client(server) as client:
        meetings = json.loads((await client.read_resource("copom://reunioes")).contents[0].text)
        minutes = (await client.read_resource("copom://atas/281")).contents[0].text
        prompt = await client.get_prompt("comparar_reunioes", {"reuniao_a": "280", "reuniao_b": "281"})
    assert meetings["total"] == 4
    assert minutes.startswith("# 281ª Reunião") and "2. O Comitê decidiu manter" in minutes
    assert "280ª e a 281ª" in prompt.messages[0].content.text


async def test_real_server_over_stdio():
    """Sobe o executável de verdade, como o Claude Desktop faria, com o corpus que vai no pacote."""
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "copom_mcp.cli"], env={"COPOM_MCP_SEMANTIC": "0", "PATH": ""}
    )
    async with Client(params) as client:
        result = await client.call_tool("listar_reunioes", {"desde": "2020-08-01", "ate": "2020-08-31"})
    assert result.structured_content["reunioes"][0]["reuniao"] == 232


async def test_list_meetings_limit_keeps_the_most_recent(server):
    async with Client(server) as client:
        result = await client.call_tool("listar_reunioes", {"limite": 2})
    data = result.structured_content
    assert data["total"] == 4 and [m["reuniao"] for m in data["reunioes"]] == [280, 281]


def daily_selic(request: httpx.Request) -> httpx.Response:
    values = [15.0, 15.0, 15.0, 14.75, 14.75, 14.5, 14.5]
    return httpx.Response(200, json=[{"data": f"0{d + 1}/03/2026", "valor": str(v)} for d, v in enumerate(values)])


async def test_selic_target_returns_only_the_changes(meetings, chunks):
    http = httpx.Client(transport=httpx.MockTransport(daily_selic))
    server = create_server(meetings, chunks, http=http, semantic=False, analyses={})
    async with Client(server) as client:
        result = await client.call_tool("consultar_indicador", {"indicador": "selic_meta", "inicio": "2026-03"})
    points = [(p["data"][-2:], p["valor"]) for p in result.structured_content["pontos"]]
    assert points == [("01", 15.0), ("04", 14.75), ("06", 14.5), ("07", 14.5)]
    assert result.structured_content["periodicidade"].startswith("mudanças")


def dollar_by_window(request: httpx.Request) -> httpx.Response:
    year = request.url.params["dataInicial"][-4:]
    return httpx.Response(200, json=[{"data": f"02/01/{year}", "valor": year[-2:]}])


async def test_long_series_keeps_the_windows_in_order(meetings, chunks):
    http = httpx.Client(transport=httpx.MockTransport(dollar_by_window))
    server = create_server(meetings, chunks, http=http, semantic=False, analyses={})
    async with Client(server) as client:
        result = await client.call_tool("consultar_indicador", {"indicador": "dolar", "inicio": "2000", "fim": "2026"})
    years = [p["data"][:4] for p in result.structured_content["pontos"]]
    assert years == sorted(years) and len(years) == 6  # 6 janelas de 5 anos, buscadas em paralelo


@pytest.mark.parametrize(
    ("name", "args", "message"),
    [
        ("analisar_reuniao", {}, "Falta o argumento reuniao"),
        ("analisar_reuniao", {"reuniao": "xyz"}, "Reunião 'xyz' não encontrada. Há atas da 30ª à 281ª"),
        ("comparar_reunioes", {"reuniao_a": "280", "reuniao_b": "999"}, "Reunião '999' não encontrada"),
        ("prompt_inexistente", {}, "Prompt desconhecido"),
    ],
)
async def test_prompt_errors_explain_the_argument(server, name, args, message):
    async with Client(server) as client:
        with pytest.raises(MCPError) as error:
            await client.get_prompt(name, args)
    assert error.value.code == INVALID_PARAMS and message in error.value.message


async def test_prompt_accepts_ordinal(server):
    async with Client(server) as client:
        prompt = await client.get_prompt("analisar_reuniao", {"reuniao": "281ª"})
    assert prompt.messages[0].content.text.startswith("Analise a 281ª reunião")
