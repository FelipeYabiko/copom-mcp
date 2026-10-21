"""Linha de comando: `copom-mcp` (servidor), `copom-mcp atualizar` e `copom-mcp buscar`."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .corpus import BCBClient, build_corpus, load_corpus, save_corpus, user_data_dir

ANALYSES_PATH = Path(__file__).parent / "data" / "analises.json"
TRACE_PATH = Path("evals/results/traces.jsonl")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="copom-mcp", description="Servidor MCP com as atas do Copom.")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("servir", help="Inicia o servidor MCP (padrão).")
    serve.add_argument("--http", action="store_true", help="Usa Streamable HTTP em vez de stdio.")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("atualizar", help="Baixa atas novas do Banco Central para a pasta do usuário.")
    extract = sub.add_parser("extrair", help="Analisa atas com o Claude (precisa de ANTHROPIC_API_KEY).")
    extract.add_argument("reunioes", nargs="*", type=int, help="Números das reuniões (padrão: as que faltam)")
    extract.add_argument("--lote", action="store_true", help="Usa a Batch API (50%% mais barata, até 24 h).")
    extract.add_argument("--saida", type=Path, default=ANALYSES_PATH)
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
        return _extract(args.reunioes, args.lote, args.saida)

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


def _extract(numbers: list[int], batch: bool, output: Path) -> int:
    import anthropic

    from .extract import ExtractionError, Extractor, load_analyses, save_analyses

    meetings, chunks = load_corpus()
    analyses = load_analyses(output)
    # Só reuniões com meta Selic definida (desde mar/1999) e ainda não analisadas.
    todo = numbers or [m.number for m in meetings if m.decision and m.number not in analyses]
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


if __name__ == "__main__":
    raise SystemExit(main())
