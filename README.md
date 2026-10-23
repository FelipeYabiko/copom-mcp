# copom-mcp

[![CI](https://github.com/FelipeYabiko/copom-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/FelipeYabiko/copom-mcp/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![MCP](https://img.shields.io/badge/MCP-servidor-purple)
![License](https://img.shields.io/badge/license-MIT-green)

<!-- mcp-name: io.github.FelipeYabiko/copom-mcp -->

Servidor **MCP (Model Context Protocol)** que dá a assistentes de IA, como Claude Desktop, Claude Code, Cursor e
VS Code, acesso às **261 atas do Copom** (de 1998 até hoje) e aos indicadores oficiais do **Banco Central do
Brasil**. Pergunte em português e receba respostas com **citação do parágrafo e da reunião** de onde veio cada
afirmação.

> "Como o Copom passou a falar das tarifas americanas em 2025?"
> "Compare o tom das duas últimas reuniões."
> "Em quais reuniões houve voto divergente e o que a minoria defendia?"

Não precisa de chave de API: o servidor só entrega os dados, e quem responde é o assistente que você já usa.

## O que tem aqui

| Conceito | Onde |
| --- | --- |
| **Servidor MCP** com ferramentas, recursos e prompts, saída estruturada (schemas Pydantic) e anotações | [`server.py`](src/copom_mcp/server.py) |
| **RAG com citações**: corpus de 13 mil parágrafos, extraídos de HTML e PDF de 28 anos de formatos | [`parsing.py`](src/copom_mcp/parsing.py), [`corpus.py`](src/copom_mcp/corpus.py) |
| **Busca híbrida**: BM25 próprio + embeddings locais (fastembed), combinados por Reciprocal Rank Fusion | [`search.py`](src/copom_mcp/search.py) |
| **Extração estruturada com o Claude**: few-shot, prompt caching, Batch API, fallback em recusas, custo por chamada | [`extract.py`](src/copom_mcp/extract.py) |
| **Evals**: 25 perguntas com gabarito para a busca e comparação da extração com a meta Selic oficial | [`evals/`](evals) |
| **Desenvolvimento com o Claude Code**: `CLAUDE.md`, hooks, skills, subagente revisor, `.mcp.json` e `@claude` no GitHub | [`.claude/`](.claude), [`CLAUDE.md`](CLAUDE.md) |
| **Entrega**: pacote com o corpus embutido, CI que testa o wheel e publicação no PyPI por trusted publishing | [`.github/workflows/`](.github/workflows) |

## Instalação

Precisa do [uv](https://docs.astral.sh/uv/) (`pip install uv`) ou de Python 3.11+.

**Claude Code**

```bash
claude mcp add copom -- uvx --from git+https://github.com/FelipeYabiko/copom-mcp copom-mcp
```

**Claude Desktop**: em Configurações → Desenvolvedor → Editar configuração, adicione:

```json
{
  "mcpServers": {
    "copom": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/FelipeYabiko/copom-mcp", "copom-mcp"]
    }
  }
}
```

**Busca semântica (opcional).** A instalação padrão usa só palavras-chave e é leve. Para a busca híbrida, use
`copom-mcp[semantic]` no lugar de `copom-mcp`. Na primeira vez, o servidor baixa um modelo de ~220 MB e gera os
embeddings, o que leva alguns minutos.

## Ferramentas

| Ferramenta | O que faz |
| --- | --- |
| `buscar_atas` | Busca parágrafos sobre um tema, com filtros de período e reunião. Devolve trechos prontos para citar |
| `ler_ata` | Texto de uma ata por seção e parágrafo, com a decisão de juros |
| `listar_reunioes` | Reuniões com a meta Selic antes e depois e a decisão (alta, corte ou manutenção) |
| `consultar_indicador` | Séries oficiais do BCB: meta Selic, IPCA (mensal e 12 meses), IGP-M e dólar PTAX |
| `analise_reuniao` | Análise estruturada gerada pelo Claude: tom, fatores, riscos e orientação futura |

Também há **recursos** (`copom://reunioes`, `copom://atas/{reuniao}`) e **prompts** (`analisar_reuniao`,
`comparar_reunioes`).

Saída real de `listar_reunioes(desde="2026")`:

```text
276ª  2026-01-28  15.00% → 15.00%  manutenção (+0 pb)
277ª  2026-03-18  15.00% → 14.75%  corte (-25 pb)
278ª  2026-04-29  14.75% → 14.50%  corte (-25 pb)
279ª  2026-06-17  14.50% → 14.25%  corte (-25 pb)
280ª  2026-08-05  14.25% → 14.00%  corte (-25 pb)
281ª  2026-09-16  14.00% → 13.75%  corte (-25 pb)
```

E de `buscar_atas("choque de tarifas comércio internacional", desde="2025")`:

```text
271ª (2025-06-18) B) Cenários e análise de riscos §6: O cenário externo mostra-se adverso e particularmente
incerto. O Comitê avaliou que houve alguns desenvolvimentos que permitiriam a conclusão de uma melhora no
cenário internacional, como a reversão parcial das tarifas, mas...

270ª (2025-05-07) B) Cenários e análise de riscos §7: O cenário externo mostra-se adverso e particularmente
incerto. O choque de tarifas e o choque de incerteza, apesar de todas as tentativas de mensuração...
```

## Avaliação

### Busca

25 perguntas, cada uma com um gabarito em forma de expressão regular sobre o texto das atas. "Diretas" usam a
palavra que está no texto; "paráfrases" descrevem o tema com outras palavras (por exemplo, "guerra comercial"
quando a ata diz "choque de tarifas").

| Modo | hit@5 | precisão@5 | MRR@10 | hit@5 (diretas) | hit@5 (paráfrases) |
| --- | --- | --- | --- | --- | --- |
| BM25 (padrão) | 0,72 | 0,54 | 0,64 | 0,90 | 0,60 |
| Híbrida + MiniLM multilíngue (220 MB) | 0,76 | 0,53 | 0,63 | 1,00 | 0,60 |
| Híbrida + potion multilíngue (estático) | 0,72 | 0,49 | 0,61 | 0,90 | 0,60 |
| Híbrida + EmbeddingGemma 300M (1,2 GB) | 0,72 | 0,50 | 0,71 | 1,00 | 0,53 |

O que os números mostram:

- **Os embeddings pequenos quase não ajudam nas paráfrases.** O MiniLM lê só 128 tokens, e os parágrafos têm
  até 450. O EmbeddingGemma, maior e mais recente, põe o resultado certo mais no topo (melhor MRR) e
  acerta todas as diretas, mas erra mais paráfrases, pesa 1,2 GB e levou 49 minutos para indexar o corpus na CPU.
  Nenhum modelo ganhou com folga, então o padrão é o MiniLM: o melhor hit@5 com 220 MB.
- **Num servidor MCP, quem escreve a busca é o modelo.** Por isso a descrição de `buscar_atas` ensina o
  vocabulário do Copom e sugere várias buscas curtas. Na prática, isso resolve boa parte das paráfrases sem
  nenhum modelo extra.
- O CI roda o eval do BM25 e falha se a qualidade cair abaixo da linha de base.

Para reproduzir: `python evals/run_busca.py` (adicione `--semantica --modelo <nome>` para os outros).

### Extração

`copom-mcp extrair` (com `ANTHROPIC_API_KEY`) analisa cada ata com o Claude e `python evals/run_extracao.py`
compara a decisão, a nova Selic e a variação com a série oficial do BCB, além de somar custo, uso de cache e
latência. A ata usada como exemplo no prompt fica fora da conta, para não inflar o resultado.

Com `--lote`, o id do lote fica em `evals/results/lote-pendente.json` assim que ele é criado. Se o programa
parar durante a espera (até 24 h), o lote segue na Anthropic: rodar de novo com `--lote` retoma o mesmo lote
em vez de criar e pagar outro, e o modo sem `--lote` se recusa a rodar enquanto houver um pendente.

**Testar sem custo.** A mesma extração roda em provedores com plano gratuito, pela API compatível com a da
OpenAI: mesmo schema e mesmas instruções, com a resposta validada pelo Pydantic e uma nova tentativa quando
vem fora do schema. Sem prompt caching nem Batch API, que são da Anthropic.

```bash
export GROQ_API_KEY=...                                   # ou GEMINI_API_KEY / OPENROUTER_API_KEY
copom-mcp extrair --provedor groq --limite 5              # as 5 atas mais recentes que faltam
python evals/run_extracao.py --analises evals/results/analises-groq.json
```

| Provedor | Modelo padrão | Observação |
| --- | --- | --- |
| `groq` | `openai/gpt-oss-120b` | Rápido, mas o plano gratuito tem poucos tokens por minuto: vai sem a ata de exemplo, e as atas longas (quase todas antes de 2016) não cabem |
| `gemini` | `gemini-3.5-flash-lite` | Aceita atas longas e vai com o exemplo; o melhor para rodar o corpus inteiro |
| `openrouter` | escolha com `--modelo` | Modelos gratuitos têm sufixo `:free` e mudam com frequência |

As análises vão para `evals/results/analises-<provedor>.json` (fora do Git), sem misturar com as do Claude que
vão no pacote. O 429 por minuto é respeitado (`retry-after`); cota do dia esgotada, chave inválida ou três atas
seguidas com erro param a execução, que continua de onde parou na próxima vez.

## Desenvolvimento

```bash
git clone https://github.com/FelipeYabiko/copom-mcp.git && cd copom-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                                   # 25 testes, sem internet
copom-mcp buscar "inflação de serviços"     # testar a busca no terminal
npx @modelcontextprotocol/inspector .venv/bin/copom-mcp   # testar o servidor numa interface web
```

| Comando | O que faz |
| --- | --- |
| `copom-mcp` | Servidor MCP por stdio (é o que os clientes executam) |
| `copom-mcp servir --http` | Servidor por Streamable HTTP |
| `copom-mcp atualizar` | Baixa atas novas para `~/.local/share/copom-mcp` |
| `copom-mcp extrair [reuniões] [--lote]` | Análise estruturada com o Claude (Batch API com `--lote`) |
| `copom-mcp extrair --provedor groq [--limite N]` | A mesma análise num provedor gratuito (`groq`, `gemini` ou `openrouter`) |
| `python scripts/build_corpus.py` | Regenera o corpus que vai no pacote |

Abrindo o repositório no **Claude Code**, ele já vem configurado:

- **Hooks:** `ruff` depois de cada edição e testes antes de encerrar uma tarefa.
- **Skills:** `/nova-serie`, `/avaliar-busca` e `/atualizar-corpus`.
- **Subagente:** `revisor`, que revisa com o checklist do projeto.
- **Servidor MCP:** o próprio servidor, via `.mcp.json`.

Comentar `@claude` numa issue ou PR aciona a Claude Code GitHub Action, depois de instalar o app e configurar o
segredo `ANTHROPIC_API_KEY`.

Quer entender cada conceito em detalhe? Veja o [guia de estudo](docs/GUIA-DE-ESTUDO.md).

## Fonte dos dados

Atas e séries: [Banco Central do Brasil](https://www.bcb.gov.br/publicacoes/atascopom/cronologicos), dados
públicos. Este projeto não tem vínculo com o Banco Central. Atas anteriores a ~2008 não numeram os parágrafos;
nelas, o número do parágrafo é a ordem no texto.

## Licença

MIT
