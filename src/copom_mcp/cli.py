"""Linha de comando: `copom-mcp` (servidor), `copom-mcp atualizar` e `copom-mcp buscar`."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from .corpus import BCBClient, build_corpus, load_corpus, save_corpus, user_data_dir
from .providers import PROVIDERS, most_recent

ANALYSES_PATH = Path(__file__).parent / "data" / "analises.json"
TRACE_PATH = Path("evals/results/traces.jsonl")
MAX_CONSECUTIVE_FAILURES = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="copom-mcp", description="Servidor MCP com as atas do Copom.")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("servir", help="Inicia o servidor MCP (padrão).")
    serve.add_argument("--http", action="store_true", help="Usa Streamable HTTP em vez de stdio.")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("atualizar", help="Baixa atas novas do Banco Central para a pasta do usuário.")
    extract = sub.add_parser("extrair", help="Analisa atas com o Claude ou com um provedor gratuito.")
    extract.add_argument("reunioes", nargs="*", type=int, help="Números das reuniões (padrão: as que faltam)")
    extract.add_argument(
        "--provedor",
        choices=["anthropic", *PROVIDERS],
        default="anthropic",
        help="anthropic (ANTHROPIC_API_KEY), groq (GROQ_API_KEY), gemini (GEMINI_API_KEY) "
        "ou openrouter (OPENROUTER_API_KEY).",
    )
    extract.add_argument("--modelo", help="Modelo do provedor gratuito (o Claude usa sempre o modelo padrão).")
    extract.add_argument("--limite", type=_positive, help="Analisa só as N reuniões mais recentes que faltam.")
    exemplo = extract.add_mutually_exclusive_group()
    exemplo.add_argument("--com-exemplo", dest="exemplo", action="store_true", default=None)
    exemplo.add_argument(
        "--sem-exemplo",
        dest="exemplo",
        action="store_false",
        help="Manda ou não a ata de exemplo (~4 mil tokens). Padrão: sem exemplo no Groq, com nos outros.",
    )
    extract.add_argument("--lote", action="store_true", help="Usa a Batch API (50%% mais barata, até 24 h).")
    extract.add_argument(
        "--saida",
        type=Path,
        help=f"Arquivo das análises. Padrão: {ANALYSES_PATH.name} do pacote para o Claude, "
        "evals/results/analises-<provedor>.json para os outros.",
    )
    search = sub.add_parser("buscar", help="Busca nas atas pelo terminal (útil para testar).")
    search.add_argument("consulta")
    search.add_argument("-n", type=int, default=5)
    args = parser.parse_args(argv)

    # Em stdio, stdout é o canal do protocolo MCP: todo log vai para stderr.
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("pypdf").setLevel(logging.ERROR)

    if args.command == "atualizar":
        meetings, chunks = build_corpus(BCBClient(), existing=load_corpus(), progress=logging.info)
        save_corpus(user_data_dir(), meetings, chunks)
        logging.info("Corpus salvo em %s: %d reuniões, %d parágrafos.", user_data_dir(), len(meetings), len(chunks))
        return 0

    if args.command == "extrair":
        if args.provedor == "anthropic":
            if args.modelo or args.exemplo is not None:
                parser.error("--modelo, --com-exemplo e --sem-exemplo valem só para os provedores gratuitos.")
            return _extract(args.reunioes, args.lote, args.saida or ANALYSES_PATH, args.limite)
        if args.lote:
            parser.error("--lote usa a Batch API da Anthropic; não existe nos outros provedores.")
        if args.provedor == "openrouter" and not args.modelo:
            parser.error("o OpenRouter precisa de --modelo (ex.: um modelo gratuito, com sufixo ':free').")
        output = args.saida or Path(f"evals/results/analises-{args.provedor}.json")
        return _extract_compat(args.provedor, args.modelo, args.exemplo, args.reunioes, output, args.limite)

    if args.command == "buscar":
        from .search import SearchEngine

        _, chunks = load_corpus()
        for hit in SearchEngine(chunks).search(args.consulta, args.n):
            c = hit.chunk
            print(f"\n{c.meeting}ª reunião ({c.date}) · {c.section} · §{c.paragraph}\n{c.text}")
        return 0

    from .server import create_server

    server = create_server()
    if getattr(args, "http", False):
        server.run("streamable-http", port=args.port)
    else:
        server.run("stdio")
    return 0


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("precisa ser maior que zero")
    return number


def _pending(meetings, analyses, numbers: list[int], limit: int | None) -> list[int]:
    # Só reuniões com meta Selic definida (desde mar/1999) e ainda não analisadas.
    todo = numbers or [m.number for m in meetings if m.decision and m.number not in analyses]
    return most_recent(todo, limit)


def _extract(numbers: list[int], batch: bool, output: Path, limit: int | None = None) -> int:
    import anthropic

    from .extract import ExtractionError, Extractor, load_analyses, save_analyses

    meetings, chunks = load_corpus()
    analyses = load_analyses(output)
    todo = _pending(meetings, analyses, numbers, limit)
    extractor = Extractor(anthropic.Anthropic(), meetings, chunks, trace_path=TRACE_PATH)
    logging.info("%d atas para analisar.", len(todo))

    if batch:
        client = extractor.client
        job = client.messages.batches.create(requests=extractor.batch_requests(todo))
        logging.info("Lote %s criado. Aguardando (pode levar até 24 h)...", job.id)
        while (job := client.messages.batches.retrieve(job.id)).processing_status != "ended":
            time.sleep(60)
        for result in client.messages.batches.results(job.id):
            number, analysis, error = Extractor.parse_batch_result(result)
            if result.result.type == "succeeded":
                extractor.record(number, result.result.message, latency=None, batch=True)
            if analysis:
                analyses[number] = analysis
            else:
                logging.warning("Reunião %d: %s", number, error)
    else:
        for number in todo:
            try:
                analyses[number] = extractor.extract(number)
                logging.info("Reunião %d: %s (%s)", number, analyses[number].decisao, analyses[number].tom)
            except ExtractionError as exc:
                logging.warning("%s", exc)
            save_analyses(output, analyses)  # salva a cada ata: dá para interromper e continuar

    save_analyses(output, analyses)
    logging.info("%d análises em %s. Custo e latência por chamada em %s.", len(analyses), output, TRACE_PATH)
    return 0


def _extract_compat(
    name: str, model: str | None, few_shot: bool | None, numbers: list[int], output: Path, limit: int | None
) -> int:
    from .extract import ExtractionError, load_analyses, save_analyses
    from .providers import CompatExtractor, ProviderUnavailable, RequestTooLarge

    provider = PROVIDERS[name]
    api_key = os.environ.get(provider.key_env)
    if not api_key:
        logging.error("Defina a chave antes: export %s=...", provider.key_env)
        return 2
    meetings, chunks = load_corpus()
    analyses = load_analyses(output)
    todo = _pending(meetings, analyses, numbers, limit)
    extractor = CompatExtractor(provider, api_key, meetings, chunks, model, few_shot, trace_path=TRACE_PATH)
    logging.info("%d atas para analisar com %s.", len(todo), extractor.label)

    status, failures = 0, 0
    for number in todo:
        try:
            analyses[number] = extractor.extract(number)
            logging.info("Reunião %d: %s (%s)", number, analyses[number].decisao, analyses[number].tom)
            failures = 0
        except RequestTooLarge as exc:
            logging.warning("%s", exc)
        except ExtractionError as exc:
            logging.warning("%s", exc)
            failures += 1
            if failures == MAX_CONSECUTIVE_FAILURES:
                logging.error("%d atas seguidas falharam; parando para não repetir o erro em todas.", failures)
                status = 1
                break
        except ProviderUnavailable as exc:
            logging.error("%s\nParando; rode de novo depois para continuar de onde parou.", exc)
            status = 1
            break
        save_analyses(output, analyses)

    save_analyses(output, analyses)
    logging.info("%d análises em %s. Tokens e latência por chamada em %s.", len(analyses), output, TRACE_PATH)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
