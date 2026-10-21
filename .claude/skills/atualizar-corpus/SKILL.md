---
name: atualizar-corpus
description: Baixa as atas novas do Copom, regenera o corpus que vai no pacote e confere se nada quebrou. Use depois de uma reunião do Copom ou quando pedirem para atualizar os dados.
disable-model-invocation: true
allowed-tools: Bash(python scripts/build_corpus.py), Bash(pytest *), Bash(python evals/*), Bash(git diff *)
---

# Atualizar o corpus

1. Rode `python scripts/build_corpus.py` (baixa tudo de novo, leva alguns minutos).
2. Compare com a versão anterior: `git diff --stat src/copom_mcp/data/`. Conte quantas reuniões e
   parágrafos entraram. Se o total de parágrafos de reuniões antigas mudou, investigue: o Banco Central pode
   ter mudado o formato do HTML, e o parser precisa de ajuste.
3. Abra a ata mais recente com a ferramenta `ler_ata` (ou `copom-mcp buscar` filtrando pela reunião) e confira
   se as seções e a numeração dos parágrafos saíram certas.
4. Rode `pytest -q` e a skill `/avaliar-busca`.
5. Mostre ao usuário: reuniões novas, decisão de juros de cada uma e qualquer anomalia. Não faça commit sem
   ele confirmar.
