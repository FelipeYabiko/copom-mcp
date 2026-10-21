from copom_mcp.search import BM25, SearchEngine, stem, tokenize


def test_tokenize_removes_accents_stopwords_and_plurals():
    assert tokenize("As expectativas de inflação dos agentes") == ["expectativa", "inflacao", "agente"]
    assert stem("juros") == "juro"
    assert tokenize("ações fiscais") == ["acao", "fiscal"]


def test_bm25_prefers_rare_terms():
    docs = [["inflacao", "servico"], ["inflacao", "petroleo"], ["inflacao", "inflacao", "credito"]]
    scores = BM25(docs).scores(["petroleo"])
    assert max(scores, key=scores.get) == 1


def test_search_finds_paragraph_and_skips_preamble(chunks):
    engine = SearchEngine(chunks)
    hits = engine.search("petróleo combustíveis")
    assert hits[0].chunk.id == "279-2"
    assert all(h.chunk.paragraph > 0 for h in engine.search("membros da diretoria"))


def test_search_filters(chunks):
    engine = SearchEngine(chunks)
    assert {h.chunk.meeting for h in engine.search("inflação", since="2026-08-01")} == {280, 281}
    assert {h.chunk.meeting for h in engine.search("inflação", meeting=281)} == {281}
    assert engine.search("Rússia", until="2000-12-31")[0].chunk.meeting == 30


class FakeSemantic:
    """Simula embeddings: diz que o parágrafo sobre serviços é o mais parecido com qualquer pergunta."""

    def __init__(self, chunks, favorite):
        self.favorite = favorite
        self.ids = [c.id for c in chunks]

    def scores(self, query):
        return [1.0 if i == self.favorite else 0.0 for i in self.ids]


def test_hybrid_search_fuses_rankings(chunks):
    engine = SearchEngine(chunks, semantic=FakeSemantic(chunks, favorite="280-1"))
    hits = engine.search("petróleo", limit=3)
    ids = [h.chunk.id for h in hits]
    # O que as duas buscas concordam vem primeiro; o que só a semântica achou também entra.
    assert ids[0] == "279-2" and "280-1" in ids
    assert engine.mode.startswith("híbrida")
