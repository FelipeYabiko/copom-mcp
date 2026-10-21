# Guia de estudo

Este é o projeto com mais conceitos atuais de IA do portfólio. O guia segue a ordem em que as peças se apoiam:
primeiro os dados, depois a busca, o protocolo MCP, o uso do Claude pela API, a avaliação e, por fim, o
desenvolvimento junto com o Claude Code. Cada tópico diz onde está no código, traz um exercício e indica onde
aprender mais.

## Parte 1: Dados

### 1. De HTML e PDF a parágrafos (parsing)

As atas existem em três formatos ao longo de 28 anos: HTML com parágrafos numerados (desde ~2008), HTML gerado
pelo Word, com frases quebradas em várias linhas e sem numeração (1998–2008), e só PDF (2016–2020). O parser
transforma tudo em texto, e um segmentador único separa seções e parágrafos.

Os problemas reais que apareceram, e como foram resolvidos:

| Problema | Solução |
| --- | --- |
| `<sup>` e `<i>` quebravam o texto no meio | Só elementos de bloco (`p`, `div`, `h3`...) viram quebra de linha |
| Número do parágrafo sozinho na linha ("1.") | O padrão aceita número sem texto depois |
| Título quebrado em duas linhas | Os títulos do sumário servem de referência para juntar as linhas |
| Atas antigas sem numeração | Modo "blocos": junta linhas que continuam a frase e numera em sequência |
| Presentes e notas de rodapé no fim do HTML | Viram o bloco de identificação, sem número |
| Cabeçalho de página no PDF | Removido por expressão regular |

- **No código:** [`parsing.py`](../src/copom_mcp/parsing.py) e os casos em [`test_parsing.py`](../tests/test_parsing.py).
- **Exercício:** escolha uma ata de 2003 e compare o texto original (`https://www.bcb.gov.br/api/servico/sitebcb/copom/atas_detalhes?nro_reuniao=80`) com o resultado de `ler_ata(80)`.
- **Lição:** estatísticas agregadas não pegaram três desses bugs; ler uma ata inteira pegou. Sempre olhe os dados.

### 2. Gabarito a partir de dados oficiais

A decisão de cada reunião (alta, corte ou manutenção) não é extraída do texto: vem da série oficial da meta
Selic (SGS 432), comparando o dia da reunião com o dia seguinte. Isso dá um **gabarito objetivo** para avaliar a
extração feita pelo Claude.

- **No código:** `attach_decisions` em [`corpus.py`](../src/copom_mcp/corpus.py).

## Parte 2: Busca (o "R" do RAG)

### 3. RAG: Retrieval-Augmented Generation

Um modelo de linguagem não sabe o que está nas 261 atas. RAG é buscar os trechos relevantes e entregá-los ao
modelo junto com a pergunta, para que a resposta se baseie nos documentos e cite as fontes. Aqui, a busca fica
no servidor MCP e a geração fica no cliente (Claude Desktop, Claude Code...).

### 4. BM25

BM25 pontua um documento pela presença dos termos da busca: termos raros valem mais (IDF), repetir o termo ajuda
mas satura (`k1`) e documentos longos são penalizados (`b`). A implementação tem 30 linhas, sem bibliotecas.
Para o português, o texto é normalizado: sem acentos, sem palavras vazias e com um stemmer mínimo de plurais
("ações" → "acao").

- **No código:** `BM25`, `tokenize` e `stem` em [`search.py`](../src/copom_mcp/search.py).
- **Bug real:** o stemmer transformava "ações" em "acoe"; o teste pegou.
- **Leia:** [Okapi BM25 (Wikipedia)](https://en.wikipedia.org/wiki/Okapi_BM25).

### 5. Embeddings e busca semântica

Um modelo de embeddings transforma um texto num vetor; textos com sentido parecido ficam próximos. Assim,
"guerra comercial" pode encontrar "choque de tarifas" mesmo sem palavras em comum. O projeto roda os modelos
localmente com [fastembed](https://github.com/qdrant/fastembed) (ONNX), sem API paga, e guarda os vetores em
cache.

- **Detalhe que importa:** cada modelo tem um limite de tokens. O MiniLM lê só 128 tokens e corta o resto de
  parágrafos que chegam a 450. Modelos que exigem prefixos ("query:", "passage:") precisam de
  `query_embed`/`passage_embed`.

### 6. Busca híbrida com Reciprocal Rank Fusion

BM25 e embeddings erram em casos diferentes. RRF junta as duas listas somando `1 / (60 + posição)` de cada uma:
não depende da escala dos scores, então dá para combinar rankings que não são comparáveis.

- **No código:** `SearchEngine.search`.
- **Exercício:** mude `RRF_K` para 10 e para 200 e rode o eval. O que acontece?

## Parte 3: MCP (Model Context Protocol)

### 7. O que é MCP

MCP é um protocolo aberto para conectar assistentes de IA a dados e ferramentas. Em vez de cada aplicativo
integrar cada fonte, a fonte vira um **servidor MCP** e qualquer cliente compatível (Claude Desktop, Claude Code,
Cursor, VS Code) passa a usá-la. A comunicação é JSON-RPC, por **stdio** (o cliente roda o servidor como
processo local) ou **Streamable HTTP** (servidor remoto).

- **Leia:** [modelcontextprotocol.io](https://modelcontextprotocol.io).

### 8. As três primitivas: ferramentas, recursos e prompts

- **Ferramentas** (`buscar_atas`, `ler_ata`...): o *modelo* decide quando chamar. A docstring é o que ele lê para
  decidir, então é parte do produto.
- **Recursos** (`copom://atas/281`): dados que o *usuário ou o aplicativo* anexam à conversa.
- **Prompts** (`analisar_reuniao`): roteiros prontos que o *usuário* escolhe.

- **No código:** [`server.py`](../src/copom_mcp/server.py).

### 9. Saída estruturada, anotações e erros

- Ferramentas que retornam modelos Pydantic publicam um **schema de saída**: o cliente recebe JSON validado,
  além do texto.
- **Anotações** (`read_only_hint`, `open_world_hint`) dizem ao cliente se a ferramenta só lê dados ou acessa a
  internet, o que permite pular confirmações.
- **Erros esperados** usam `ToolError`: a mensagem chega ao modelo ("há atas da 21ª à 281ª") e ele se corrige.
  Qualquer outra exceção vira "erro inesperado", sem detalhes, para não vazar informação.
- **stdio:** stdout é o canal do protocolo. Um `print` no lugar errado corrompe a conversa; por isso todo log vai
  para stderr.

- **Exercício:** crie uma ferramenta `proxima_reuniao` que devolva a data da próxima reunião do Copom.
- **Atenção:** a versão 2 do SDK renomeou `FastMCP` para `MCPServer`. Tutoriais antigos usam o nome velho.

### 10. Testar um servidor MCP

Os testes conectam o cliente oficial ao servidor em processo (`Client(server)`) e chamam as ferramentas pelo
protocolo de verdade. Um teste sobe o executável por stdio, como o Claude Desktop faria.

- **No código:** [`test_server.py`](../tests/test_server.py).
- **Ferramenta útil:** [MCP Inspector](https://github.com/modelcontextprotocol/inspector), interface web para
  testar servidores na mão: `npx @modelcontextprotocol/inspector .venv/bin/copom-mcp`.

## Parte 4: Usando o Claude pela API

### 11. Extração estruturada

O Claude lê uma ata e preenche um modelo Pydantic (`AnaliseAta`): decisão, tom, fatores, riscos e orientação
futura. Com `messages.parse(output_format=...)`, a API garante JSON válido no formato do schema.

- **No código:** [`extract.py`](../src/copom_mcp/extract.py).
- **Leia:** [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).

### 12. Few-shot e prompt caching

O prompt de sistema traz uma ata real (a 281ª) já analisada. Esse exemplo melhora a consistência e faz o prefixo
ter ~3,5 mil tokens, idênticos em todas as chamadas. Com `cache_control`, a API guarda esse prefixo: nas chamadas
seguintes, ele custa 5% do preço normal.

- **Regra de ouro:** cache é por prefixo exato. Coisas variáveis (a ata do dia) vêm depois do que é fixo.
- **Leia:** [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).

### 13. Batch API, recusas e fallbacks

- A **Batch API** processa as 260 atas de forma assíncrona com 50% de desconto. Ela não aceita o helper
  `parse`, então o schema vai em `output_config.format` (gerado com `anthropic.transform_schema`).
- Toda resposta é checada: `stop_reason` igual a `refusal` ou `max_tokens` vira erro claro, não um JSON quebrado.
- No modo síncrono, `fallbacks: "default"` faz a API tentar outro modelo se o principal recusar.

### 14. Observabilidade e custo

Cada chamada grava tokens (normais e de cache), custo estimado e latência num JSONL. O eval de extração soma
tudo: custo total, fração da entrada servida pelo cache e latência média. Medir custo por tarefa é o que separa
uma demo de um sistema que alguém pode pagar para rodar.

- **Exercício:** rode `copom-mcp extrair 280 279` duas vezes seguidas e compare `cache_read_tokens` entre as
  chamadas.

## Parte 5: Avaliação (evals)

### 15. Por que evals

Sem medir, "melhorei a busca" é opinião. Um eval é um conjunto fixo de casos com gabarito e uma métrica. Toda
mudança roda no mesmo conjunto, e o resultado decide se ela entra.

### 16. O eval de busca

25 perguntas, cada uma com um gabarito em forma de expressão regular sobre o texto. As perguntas são de dois
tipos: **diretas** (usam a palavra do texto) e **paráfrases** (descrevem o tema com outras palavras, como
"guerra comercial" para "choque de tarifas"). Métricas: hit@5, precisão@5 e MRR@10.

- **No código:** [`evals/busca.jsonl`](../evals/busca.jsonl) e [`evals/run_busca.py`](../evals/run_busca.py).
- **No CI:** `test_search_quality_does_not_regress` falha se a busca cair abaixo da linha de base.
- **Regra:** nunca ajustar o gabarito para a métrica subir.

### 17. O eval de extração

Compara a decisão, a Selic e a variação extraídas pelo Claude com a série oficial. O tom não tem gabarito
objetivo, então o eval mostra a distribuição para inspeção. A ata do exemplo few-shot fica de fora: o modelo
viu a resposta dela no prompt, então incluí-la inflaria a nota (vazamento de dados).

## Parte 6: Desenvolvimento junto com o Claude Code

### 18. `CLAUDE.md`

É o "manual do projeto" que o Claude Code lê no início de cada sessão: comandos, arquitetura e regras que não
estão óbvias no código (por exemplo, "nomes de ferramentas são contrato público").

- **No código:** [`CLAUDE.md`](../CLAUDE.md).

### 19. Hooks

Comandos que o Claude Code roda automaticamente em eventos. Aqui, depois de cada edição, o `ruff` formata e
verifica o arquivo; se sobrar erro, o hook sai com código 2 e a mensagem volta para o Claude corrigir na hora.
Antes de encerrar uma tarefa, os testes rodam; se falharem, o Claude é impedido de parar (uma vez, para não
entrar em loop).

- **No código:** [`.claude/settings.json`](../.claude/settings.json) e [`.claude/hooks/`](../.claude/hooks).

### 20. Skills e subagentes

- **Skills** são procedimentos reutilizáveis (`/nova-serie`, `/avaliar-busca`, `/atualizar-corpus`) que o Claude
  carrega quando o pedido combina com a descrição. `allowed-tools` libera só os comandos necessários.
- **Subagentes** são assistentes especializados com ferramentas e prompt próprios. O `revisor` só lê e roda
  testes, e revisa com o checklist do projeto.

- **No código:** [`.claude/skills/`](../.claude/skills) e [`.claude/agents/revisor.md`](../.claude/agents/revisor.md).

### 21. MCP no próprio projeto e Claude no GitHub

- O [`.mcp.json`](../.mcp.json) registra o próprio servidor para quem desenvolve: o Claude Code consegue testar
  as ferramentas enquanto programa.
- O workflow [`claude.yml`](../.github/workflows/claude.yml) usa a Claude Code GitHub Action: mencione `@claude`
  numa issue ou PR e ele responde, implementa ou revisa.

## Parte 7: Entrega

### 22. Empacotamento e publicação

O corpus (2,3 MB) vai dentro do pacote, então `uvx copom-mcp` funciona offline. O CI instala o wheel num ambiente
limpo para garantir isso. A publicação no PyPI usa *trusted publishing* (OIDC): o GitHub prova ao PyPI quem está
publicando, sem token guardado. O [`server.json`](../server.json) descreve o servidor para o registro oficial de
servidores MCP.

## Próximos passos

- **Reranking:** reordenar os 20 primeiros resultados com um modelo cross-encoder ou com o próprio Claude.
- **Contextual retrieval:** antes de gerar os embeddings, pedir ao Claude uma frase de contexto para cada
  parágrafo ("este trecho da ata de jun/2018 fala de...").
- **Servidor remoto:** publicar com Streamable HTTP e autenticação OAuth, para uso sem instalar nada.
- **Boletim Focus:** expor as expectativas de mercado (API Olinda do BCB) e calcular o juro real ex-ante.
