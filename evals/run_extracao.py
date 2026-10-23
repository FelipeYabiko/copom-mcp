"""Eval da extração estruturada: compara o que o modelo extraiu com a meta Selic oficial (SGS 432).

A decisão, a nova Selic e a variação em pontos-base têm gabarito objetivo. O tom não tem, então
medimos só a consistência: corte com tom "duro" é possível, mas vale olhar caso a caso. A ata do
exemplo few-shot (281ª) fica de fora, porque o modelo viu a resposta dela no prompt.

Uso (depois de `copom-mcp extrair`):
    python evals/run_extracao.py
    python evals/run_extracao.py --analises evals/results/analises-groq.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from copom_mcp.corpus import load_corpus
from copom_mcp.extract import FEW_SHOT_MEETING, load_analyses

ANALYSES = Path(__file__).parent.parent / "src" / "copom_mcp" / "data" / "analises.json"
TRACES = Path(__file__).parent / "results" / "traces.jsonl"


def evaluate(meetings, analyses) -> dict:
    rows = []
    for m in meetings:
        a = analyses.get(m.number)
        if a is None or m.decision is None or m.number == FEW_SHOT_MEETING:
            continue
        cited = set(a.paragrafos_citados)
        rows.append(
            {
                "reuniao": m.number,
                "decisao_ok": a.decisao == m.decision,
                "selic_ok": abs(a.selic_nova - m.selic_after) < 0.005,
                "variacao_ok": a.variacao_pontos_base == m.change_bp,
                "unanime": a.unanime,
                "tom": a.tom,
                "citacoes_validas": bool(cited),
            }
        )
    n = len(rows)
    rate = lambda key: round(sum(r[key] for r in rows) / n, 3) if n else None  # noqa: E731
    return {
        "n": n,
        "acuracia_decisao": rate("decisao_ok"),
        "acuracia_selic": rate("selic_ok"),
        "acuracia_variacao": rate("variacao_ok"),
        "tons": dict(Counter(r["tom"] for r in rows)),
        "divergencias": [r["reuniao"] for r in rows if not (r["decisao_ok"] and r["selic_ok"])],
    }


def cost_summary(traces: list[dict]) -> dict:
    """Chamadas, custo, uso de cache e latência, separados por modelo (ex.: Claude e groq:openai/gpt-oss-120b)."""
    by_model: dict[str, list[dict]] = {}
    for t in traces:
        by_model.setdefault(t["model"], []).append(t)
    summary = {}
    for model, rows in by_model.items():
        total_input = sum(t["input_tokens"] + t["cache_read_tokens"] + t["cache_write_tokens"] for t in rows)
        timed = [t["latency_s"] for t in rows if t["latency_s"] >= 0]
        summary[model] = {
            "chamadas": len(rows),
            "tokens_entrada": total_input,
            "tokens_saida": sum(t["output_tokens"] for t in rows),
            "custo_total_usd": round(sum(t["cost_usd"] for t in rows), 2),
            "fracao_entrada_do_cache": round(sum(t["cache_read_tokens"] for t in rows) / max(total_input, 1), 3),
            "latencia_media_s": round(sum(timed) / len(timed), 1) if timed else None,
        }
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compara as análises extraídas com a meta Selic oficial.")
    parser.add_argument(
        "--analises",
        type=Path,
        default=ANALYSES,
        help="Arquivo de análises (ex.: evals/results/analises-groq.json). Padrão: o do pacote (Claude).",
    )
    args = parser.parse_args()
    meetings, _ = load_corpus()
    analyses = load_analyses(args.analises)
    if not analyses:
        raise SystemExit(f"Nenhuma análise em {args.analises}. Rode `copom-mcp extrair` antes.")
    traces = [json.loads(line) for line in TRACES.read_text().splitlines() if line] if TRACES.exists() else []
    print(
        json.dumps(
            {"qualidade": evaluate(meetings, analyses), "custo": cost_summary(traces)}, ensure_ascii=False, indent=1
        )
    )
