"""Busca nas atas: BM25 (palavras-chave) e, opcionalmente, embeddings locais, combinados por RRF."""

from __future__ import annotations

import logging
import math
import re
import threading
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .corpus import Chunk

log = logging.getLogger(__name__)

# Palavras muito comuns em português que não ajudam a diferenciar parágrafos.
STOPWORDS = set(
    """a ao aos aquela aquelas aquele aqueles aquilo as ate com como da das de dela delas dele deles depois do dos
    e ela elas ele eles em entre era eram essa essas esse esses esta estao estas este estes eu foi foram ha isso
    isto ja la lhe lhes mais mas me mesmo meu minha muito na nas nem no nos nossa nosso num numa o os ou para pela
    pelas pelo pelos por qual quando que quem se sem ser seu seus sua suas tambem te tem ter um uma umas uns vos
    sobre sao sera seria foi ainda bem cada""".split()
)
RRF_K = 60
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def stem(token: str) -> str:
    """Stemmer mínimo: une singular e plural mais comuns (juros/juro, ações/ação, fiscais/fiscal)."""
    rules = (("oes", "ao", 2), ("aes", "ao", 2), ("ais", "al", 2), ("eis", "el", 2), ("res", "r", 3), ("s", "", 3))
    for suffix, replacement, min_stem in rules:
        if token.endswith(suffix) and len(token) - len(suffix) >= min_stem:
            return token[: -len(suffix)] + replacement
    return token


def tokenize(text: str) -> list[str]:
    return [stem(t) for t in re.findall(r"[a-z0-9]+", normalize(text)) if t not in STOPWORDS and len(t) > 1]


class BM25:
    """Okapi BM25: pontua termos raros mais alto e evita que parágrafos longos dominem."""

    def __init__(self, documents: Sequence[list[str]], k1: float = 1.4, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.doc_len = [len(d) for d in documents]
        self.avg_len = sum(self.doc_len) / max(len(documents), 1)
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for doc_id, tokens in enumerate(documents):
            for term, freq in Counter(tokens).items():
                self.postings[term].append((doc_id, freq))
        n = len(documents)
        self.idf = {t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()}

    def scores(self, query: list[str]) -> dict[int, float]:
        scores: dict[int, float] = defaultdict(float)
        for term in set(query):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for doc_id, freq in self.postings[term]:
                norm = self.k1 * (1 - self.b + self.b * self.doc_len[doc_id] / self.avg_len)
                scores[doc_id] += idf * freq * (self.k1 + 1) / (freq + norm)
        return scores


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    matched_by: tuple[str, ...]


class SemanticIndex:
    """Embeddings locais com fastembed (instale com `pip install copom-mcp[semantic]`)."""

    def __init__(self, chunks: Sequence[Chunk], cache_dir: Path, model: str = EMBEDDING_MODEL) -> None:
        import numpy as np
        from fastembed import TextEmbedding

        self._np = np
        self._model = TextEmbedding(model, cache_dir=str(cache_dir / "models"))
        slug = model.split("/")[-1]
        cache = cache_dir / f"embeddings-{slug}-{len(chunks)}-{_fingerprint(chunks)}.npy"
        if cache.exists():
            self.matrix = np.load(cache)
        else:
            log.info("Gerando embeddings de %d parágrafos (só na primeira vez)...", len(chunks))
            vectors = list(self._model.passage_embed([c.text for c in chunks], batch_size=64))
            self.matrix = _unit(np.asarray(vectors, dtype=np.float32), np)
            cache.parent.mkdir(parents=True, exist_ok=True)
            np.save(cache, self.matrix.astype(np.float16))

    def scores(self, query: str) -> list[float]:
        vector = _unit(self._np.asarray(next(iter(self._model.query_embed([query]))), dtype=self._np.float32), self._np)
        return (self.matrix @ vector).tolist()


class SearchEngine:
    def __init__(self, chunks: Sequence[Chunk], semantic: SemanticIndex | None = None) -> None:
        self.chunks = list(chunks)
        self.bm25 = BM25([tokenize(f"{c.section} {c.text}") for c in self.chunks])
        self.semantic = semantic
        self.loading = False
        self.glossary = _glossary(self.chunks)

    @property
    def mode(self) -> str:
        if self.semantic:
            return "híbrida (BM25 + embeddings)"
        if self.loading:
            return "palavras-chave (BM25); a busca semântica ainda está carregando"
        return "palavras-chave (BM25)"

    def load_semantic(self, build: Callable[[], SemanticIndex | None]) -> threading.Thread:
        """Carrega o índice semântico em outra thread. Na primeira vez, baixar o modelo e gerar os embeddings
        leva minutos, e o cliente MCP pode desistir de esperar o servidor iniciar. Até o índice ficar pronto,
        a busca usa só o BM25."""

        def run() -> None:
            try:
                self.semantic = build()
                if self.semantic:
                    log.info("Busca híbrida pronta.")
            except Exception:
                log.exception("Falha ao carregar a busca semântica; seguindo só com BM25.")
            finally:
                self.loading = False

        self.loading = True
        thread = threading.Thread(target=run, name="indice-semantico", daemon=True)
        thread.start()
        return thread

    def search(
        self,
        query: str,
        limit: int = 8,
        since: str | None = None,
        until: str | None = None,
        meeting: int | None = None,
    ) -> list[Hit]:
        allowed = [
            i
            for i, c in enumerate(self.chunks)
            if (since is None or c.date >= since)
            and (until is None or c.date <= until)
            and (meeting is None or c.meeting == meeting)
            and c.paragraph > 0  # o preâmbulo (lista de presentes) não entra na busca
            and i not in self.glossary
        ]
        allowed_set = set(allowed)
        rankings: dict[str, list[int]] = {}

        bm25 = self.bm25.scores(tokenize(query))
        rankings["bm25"] = sorted((i for i in bm25 if i in allowed_set), key=lambda i: -bm25[i])[:200]
        semantic = self.semantic
        # Se nenhuma palavra da consulta aparece no corpus ("", "xyzqwerty"), a busca semântica sozinha devolve
        # vizinhos ao acaso. Similaridade mínima não resolve: consultas boas e ruins ficam na mesma faixa.
        if semantic and bm25:
            similarity = semantic.scores(query)
            rankings["semântica"] = sorted(allowed, key=lambda i: -similarity[i])[:200]

        # Reciprocal Rank Fusion: soma 1/(k + posição) de cada lista. Não depende da escala dos scores.
        fused: dict[int, float] = defaultdict(float)
        sources: dict[int, list[str]] = defaultdict(list)
        for name, ranking in rankings.items():
            for position, i in enumerate(ranking):
                fused[i] += 1 / (RRF_K + position + 1)
                sources[i].append(name)
        best = sorted(fused, key=lambda i: -fused[i])[:limit]
        return [Hit(self.chunks[i], round(fused[i], 5), tuple(sources[i])) for i in best]


def _glossary(chunks: Sequence[Chunk]) -> set[int]:
    """Glossários de siglas das atas de 2001-2003 ("IRRFImposto sobre a Renda Retido na Fonte") não são
    conteúdo. Quase todos ficam na seção "Glossário"; na 68ª, o título ficou no texto do parágrafo, e o
    glossário vai dali até o fim da seção."""
    skip: set[int] = set()
    inside: tuple[int, str] | None = None
    for i, c in enumerate(chunks):
        if normalize(c.text).startswith("glossario "):
            inside = (c.meeting, c.section)
        if normalize(c.section).startswith("glossario") or inside == (c.meeting, c.section):
            skip.add(i)
        else:
            inside = None
    return skip


def _unit(matrix, np):
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return matrix / np.maximum(norms, 1e-12)


def _fingerprint(chunks: Sequence[Chunk]) -> str:
    import hashlib

    digest = hashlib.sha1()
    for c in chunks:
        digest.update(f"{c.id}:{len(c.text)}".encode())
    return digest.hexdigest()[:10]
