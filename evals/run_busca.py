"""Eval de busca: mede se as perguntas encontram parágrafos relevantes.

O gabarito de cada pergunta é uma expressão regular aplicada ao texto das atas: um parágrafo é
relevante se ela casa. As perguntas evitam as palavras exatas do gabarito ("paráfrase") quando
possível, para medir se a busca entende o tema e não só a palavra.

Uso:
    python evals/run_busca.py                       # BM25
    python evals/run_busca.py --semantica           # híbrida, com o modelo padrão
    python evals/run_busca.py --semantica --modelo google/embeddinggemma-300m
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

from copom_mcp.corpus import load_corpus, user_data_dir
from copom_mcp.search import EMBEDDING_MODEL, SearchEngine, SemanticIndex

EVALS = Path(__file__).parent


def load_questions(path: Path = EVALS / "busca.jsonl") -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate(engine: SearchEngine, questions: list[dict], k: int = 5) -> dict:
    rows = []
    for q in questions:
        pattern = re.compile(q["gabarito"], re.IGNORECASE)
        hits = engine.search(q["pergunta"], limit=10)
        relevant = [bool(pattern.search(h.chunk.text)) for h in hits]
        first = next((i for i, r in enumerate(relevant) if r), None)
        rows.append(
            {
                "id": q["id"],
                "tipo": q["tipo"],
                "hit@5": any(relevant[:k]),
                "precisao@5": sum(relevant[:k]) / k,
                "mrr@10": 0.0 if first is None else 1 / (first + 1),
            }
        )

    def summary(selected: list[dict]) -> dict:
        return {
            metric: round(statistics.mean(r[metric] for r in selected), 3)
            for metric in ("hit@5", "precisao@5", "mrr@10")
        } | {"n": len(selected)}

    return {
        "modo": engine.mode,
        "geral": summary(rows),
        "por_tipo": {t: summary([r for r in rows if r["tipo"] == t]) for t in sorted({r["tipo"] for r in rows})},
        "perguntas": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--semantica", action="store_true")
    parser.add_argument("--modelo", default=EMBEDDING_MODEL)
    args = parser.parse_args()

    _, chunks = load_corpus()
    semantic = SemanticIndex(chunks, user_data_dir(), model=args.modelo) if args.semantica else None
    report = evaluate(SearchEngine(chunks, semantic), load_questions())
    label = args.modelo.split("/")[-1] if args.semantica else "bm25"
    out = EVALS / "results" / f"busca-{label}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n{report['modo']} ({label})")
    print(f"  geral: {report['geral']}")
    for tipo, s in report["por_tipo"].items():
        print(f"  {tipo}: {s}")
    print("  erros (sem acerto no top 5):", [r["id"] for r in report["perguntas"] if not r["hit@5"]])


if __name__ == "__main__":
    main()
