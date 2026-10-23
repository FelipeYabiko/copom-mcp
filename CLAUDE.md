# copom-mcp

Servidor MCP que dá a assistentes de IA acesso às atas do Copom (1998 até hoje) e a indicadores do Banco
Central. Python 3.11+, SDK `mcp` 2.x (`MCPServer`, não o antigo `FastMCP`).

## Comandos

- Testes: `pytest -q` (não acessam a internet; os marcados `network` só rodam com `-m network`)
- Lint e formatação: `ruff check . && ruff format --check .`
- Eval de busca: `python evals/run_busca.py` (adicione `--semantica` para a busca híbrida)
- Eval de extração: `python evals/run_extracao.py` (precisa de `copom-mcp extrair` antes; para um provedor
  gratuito, `--analises evals/results/analises-groq.json`)
- Servidor local: `copom-mcp` (stdio) ou `copom-mcp servir --http`
- Busca pelo terminal: `copom-mcp buscar "inflação de serviços"`

## Arquitetura

- `src/copom_mcp/parsing.py`: HTML/PDF da ata → parágrafos. Dois modos: numerado (atas desde ~2008) e
  blocos (atas antigas, sem numeração). Qualquer mudança aqui exige rodar o eval de busca.
- `src/copom_mcp/corpus.py`: download da API do BCB, decisões de juros (série SGS 432) e leitura do corpus.
- `src/copom_mcp/search.py`: BM25 próprio + embeddings opcionais (fastembed), combinados por RRF.
- `src/copom_mcp/server.py`: ferramentas, recursos e prompts MCP. As saídas são modelos Pydantic.
- `src/copom_mcp/extract.py`: extração estruturada com o Claude (few-shot, cache, Batch API).
- `src/copom_mcp/providers.py`: a mesma extração em provedores gratuitos compatíveis com a OpenAI (Groq,
  Gemini, OpenRouter), via `httpx`. Nunca usar o SDK da OpenAI aqui: o projeto não depende dele.
- `src/copom_mcp/data/`: corpus gerado por `scripts/build_corpus.py`. Nunca edite à mão.
- `evals/`: perguntas com gabarito e scripts de avaliação.

## Regras

- Nomes de ferramentas, parâmetros e campos de saída do MCP são contrato público: não renomeie sem
  manter compatibilidade, porque clientes (e prompts de usuários) dependem deles.
- Erros esperados em ferramentas usam `ToolError` com uma mensagem que ajude o modelo a se corrigir.
  Qualquer outra exceção vira "erro inesperado" e a mensagem não chega ao cliente.
- Em stdio, stdout é o canal do protocolo: logs sempre em stderr, nunca `print` no servidor.
- Se uma mudança melhora o eval de busca, atualize `BM25_BASELINE` em `tests/test_evals.py`; se piora,
  explique por quê antes de aceitar.
- Textos, docstrings e mensagens em português; identificadores de código em inglês.
- A ata 281 é o exemplo few-shot da extração: ela não entra no eval de extração.
