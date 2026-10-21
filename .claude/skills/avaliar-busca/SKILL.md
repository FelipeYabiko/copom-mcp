---
name: avaliar-busca
description: Roda o eval de busca, compara com a linha de base e explica o que melhorou ou piorou. Use depois de mudar parsing.py, search.py ou o corpus, ou quando pedirem para medir a qualidade da busca.
allowed-tools: Bash(python evals/*), Bash(git diff *)
---

# Avaliar a busca

Estado atual do eval: !`python evals/run_busca.py 2>/dev/null | tail -6`

1. Compare o resultado acima com `BM25_BASELINE` em `tests/test_evals.py`.
2. Para cada pergunta que passou a errar (ou a acertar), abra `evals/results/busca-bm25.json`, rode
   `copom-mcp buscar "<pergunta>"` e explique em uma frase por que o resultado mudou.
3. Se a mudança melhorou o geral, proponha atualizar `BM25_BASELINE` para os novos valores (arredondados para
   baixo em 2 casas). Se piorou, não atualize: diga o que regrediu e sugira como corrigir.
4. Se o usuário tiver o extra `semantic` instalado, rode também `python evals/run_busca.py --semantica` e
   mostre as duas linhas lado a lado numa tabela.
5. Nunca ajuste as perguntas ou os gabaritos de `evals/busca.jsonl` para a métrica subir. Se um gabarito
   estiver errado, mostre a evidência e peça confirmação antes de mudar.
