"""Evals que rodam no CI: protegem a qualidade da busca e conferem o eval da extração."""

import sys
from pathlib import Path

from copom_mcp.corpus import load_corpus
from copom_mcp.extract import FEW_SHOT_ANSWER, AnaliseAta, load_analyses
from copom_mcp.search import SearchEngine

sys.path.insert(0, str(Path(__file__).parent.parent))
from evals.run_busca import evaluate as evaluate_search  # noqa: E402
from evals.run_busca import load_questions  # noqa: E402
from evals.run_extracao import ANALYSES as PACKAGED_ANALYSES  # noqa: E402
from evals.run_extracao import evaluate as evaluate_extraction  # noqa: E402

# Linha de base medida com o corpus atual. Se uma mudança derrubar a busca, o CI acusa.
BM25_BASELINE = {"hit@5": 0.72, "mrr@10": 0.63}


def test_search_quality_does_not_regress():
    _, chunks = load_corpus()
    report = evaluate_search(SearchEngine(chunks), load_questions())
    for metric, minimum in BM25_BASELINE.items():
        assert report["geral"][metric] >= minimum, (metric, report["geral"])


def test_packaged_analyses_cover_every_decision_and_cite_real_paragraphs():
    """`analise_reuniao` lê este arquivo: sem uma reunião nele, a ferramenta falha para ela."""
    meetings, chunks = load_corpus()
    analyses = load_analyses(PACKAGED_ANALYSES)
    assert {m.number for m in meetings if m.decision} <= set(analyses)
    report = evaluate_extraction(meetings, analyses)
    assert (report["acuracia_decisao"], report["acuracia_selic"]) == (1.0, 1.0), report["divergencias"]
    paragraphs = {(c.meeting, c.paragraph) for c in chunks}
    missing = [(n, p) for n, a in analyses.items() for p in a.paragrafos_citados if (n, p) not in paragraphs]
    assert not missing


def test_extraction_eval_checks_against_official_selic(meetings):
    wrong = AnaliseAta(**{**FEW_SHOT_ANSWER.model_dump(), "decisao": "alta", "selic_nova": 14.5})
    right = AnaliseAta(**{**FEW_SHOT_ANSWER.model_dump(), "selic_nova": 14.0, "variacao_pontos_base": -25})
    report = evaluate_extraction(meetings, {279: wrong, 280: right, 281: FEW_SHOT_ANSWER})
    assert report["n"] == 2  # a 281ª (exemplo do few-shot) fica de fora
    assert report["acuracia_decisao"] == 0.5
    assert report["divergencias"] == [279]
