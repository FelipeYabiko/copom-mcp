import threading

from copom_mcp.corpus import Chunk
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


def test_semantic_alone_returns_nothing_when_no_word_is_in_the_corpus(chunks):
    engine = SearchEngine(chunks, semantic=FakeSemantic(chunks, favorite="280-1"))
    assert engine.search("xyzqwerty") == []
    assert engine.search("de a o") == []  # só stopwords


def test_glossary_entries_are_not_searched(chunks):
    glossary = [
        Chunk(68, "2002-02-20", "Diretrizes", 3, "Glossário a.a. ao ano a.m. ao mês"),
        Chunk(68, "2002-02-20", "Diretrizes", 4, "IPCAÍndice Nacional de Preços ao Consumidor Amplo"),
        Chunk(69, "2002-03-20", "Glossário", 9, "IGP-MÍndice Geral de Preços do Mercado"),
    ]
    engine = SearchEngine([*chunks, *glossary])
    assert engine.search("Índice Consumidor Amplo") == []
    assert engine.search("inflação") != []


def test_semantic_index_loads_in_background(chunks):
    ready = threading.Event()

    def build():
        ready.wait(5)
        return FakeSemantic(chunks, favorite="280-1")

    engine = SearchEngine(chunks)
    thread = engine.load_semantic(build)
    assert "carregando" in engine.mode
    assert [h.chunk.id for h in engine.search("petróleo")] == ["279-2"]  # só BM25 por enquanto
    ready.set()
    thread.join(5)
    assert engine.mode.startswith("híbrida") and "280-1" in [h.chunk.id for h in engine.search("petróleo")]


def test_failed_semantic_index_falls_back_to_bm25(chunks):
    def build():
        raise OSError("sem rede para baixar o modelo")

    engine = SearchEngine(chunks)
    engine.load_semantic(build).join(5)
    assert engine.mode == "palavras-chave (BM25)"
