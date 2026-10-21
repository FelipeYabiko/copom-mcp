"""Transforma o HTML (ou o PDF) de uma ata do Copom em seções e parágrafos numerados."""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString

BLOCK_TAGS = ["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "table", "body", "section", "blockquote"]

# Parágrafo numerado: "12. O Comitê..." (a maioria das atas desde 1998 segue esse padrão).
NUMBERED = re.compile(r"^(\d{1,3})\s?[.\-–](?:\s+|$)")
# Seções das atas recentes: "A) Atualização da conjuntura econômica..."
LETTER_HEADING = re.compile(r"^[A-H]\)\s+\S")
# Cabeçalho e rodapé das páginas dos PDFs.
PAGE_NOISE = re.compile(r"^(Banco Central do Brasil\s+\d+ª Reunião do Copom|\d{1,3}|Página \d+( de \d+)?)$")
MAX_CHARS = 1800
FOOTNOTES = "Notas de rodapé"
TRAILER_HEADINGS = {"notas de rodapé", "presentes", "membros do copom"}
# Legenda da tabela de projeções, que sobra no texto depois que a tabela é removida.
TABLE_CAPTION = re.compile(r"\s*Tabela \d+ Projeções\b.*$")


@dataclass(frozen=True)
class Paragraph:
    number: int  # número impresso na ata; 0 para o preâmbulo (data, local, participantes)
    section: str
    text: str


def html_to_lines(html: str) -> tuple[list[str], set[str]]:
    """Converte HTML em linhas de texto e devolve também os títulos do sumário (atas antigas)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "sup", "table"]):
        tag.decompose()  # notas de rodapé e tabelas de projeções não servem para busca em texto corrido

    toc = {
        _clean(a.get_text(" "))
        for a in soup.find_all("a", href=True)
        if a["href"].startswith("#") and len(a.get_text(strip=True)) > 3
    }
    for a in soup.find_all("a", href=lambda h: bool(h) and h.startswith("#")):
        a.decompose()  # o sumário em si não é conteúdo

    for br in soup.find_all("br"):
        br.replace_with(NavigableString("\n"))
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before(NavigableString("\n"))
        tag.insert_after(NavigableString("\n"))
        if tag.name.startswith("h") and len(tag.name) == 2:
            tag.insert_before(NavigableString("\n§ "))  # marca títulos explícitos

    lines = [_clean(line) for line in soup.get_text("").split("\n")]
    return [line for line in lines if line], {t for t in toc if t}


def pdf_to_lines(data: bytes) -> list[str]:
    from pypdf import PdfReader

    text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    lines = [_clean(line) for line in text.split("\n")]
    return [line for line in lines if line and not PAGE_NOISE.match(line)]


def segment(lines: list[str], headings: set[str] | None = None) -> list[Paragraph]:
    """Agrupa linhas em parágrafos, acompanhando a seção corrente.

    Atas a partir de ~2008 numeram os parágrafos ("12. O Comitê..."); as mais antigas não, e o
    HTML delas quebra frases em várias linhas. Tentamos o modo numerado e, se ele não encontrar
    parágrafos suficientes, caímos no modo de blocos, que numera os parágrafos em sequência.
    """
    keys = {_key(h) for h in (headings or set())}
    lines = _merge_broken_headings(lines, keys)
    numbered = _segment_numbered(lines, keys)
    if sum(1 for p in numbered if p.number > 0) >= 5:
        return numbered
    return _segment_blocks(lines, keys)


def _segment_numbered(lines: list[str], headings: set[str]) -> list[Paragraph]:
    section = "Identificação"
    current_number = 0
    trailer = False  # nas atas recentes, notas de rodapé e lista de presentes vêm depois do texto
    buffer: list[str] = []
    result: list[Paragraph] = []

    def flush() -> None:
        text = TABLE_CAPTION.sub("", _clean(" ".join(buffer)))
        buffer.clear()
        if section == FOOTNOTES:
            return
        if len(text) >= 40:
            number = 0 if trailer else current_number
            result.extend(Paragraph(number, "Identificação" if trailer else section, c) for c in _split_long(text))

    for line in lines:
        is_heading = line.startswith("§ ") or LETTER_HEADING.match(line) or _key(line) in headings
        if is_heading and len(line) < 140:
            flush()
            section = line.removeprefix("§ ").strip()
            trailer = trailer or (current_number > 0 and _key(section) in TRAILER_HEADINGS)
            continue
        if current_number > 0 and line.startswith(("Data:", "Presentes:")):
            flush()
            trailer = True
        match = NUMBERED.match(line)
        if not trailer and match and (int(match.group(1)) == current_number + 1 or current_number == 0):
            flush()
            current_number = int(match.group(1))
            line = line[match.end() :]
        buffer.append(line)
    flush()
    return _drop_empty_preamble(result)


def _segment_blocks(lines: list[str], headings: set[str]) -> list[Paragraph]:
    section = "Identificação"
    buffer: list[str] = []
    preamble: list[str] = []
    paragraphs: list[tuple[str, str]] = []

    def flush() -> None:
        text = _clean(" ".join(buffer))
        buffer.clear()
        if section == "Identificação" and (len(text) < 200 or not paragraphs and _looks_like_roster(text)):
            preamble.append(text)  # data, local e lista de presentes
        elif len(text) >= 40:
            paragraphs.extend((section, chunk) for chunk in _split_long(text))

    for line in lines:
        if line.startswith("§ ") or LETTER_HEADING.match(line) or _key(line) in headings:
            flush()
            section = line.removeprefix("§ ").strip()
            continue
        if buffer and _continues(buffer[-1], line):
            buffer.append(line)
        else:
            flush()
            buffer.append(line)
    flush()

    result = [Paragraph(0, "Identificação", " ".join(preamble)[: MAX_CHARS * 2])] if preamble else []
    result += [Paragraph(i, sec, text) for i, (sec, text) in enumerate(paragraphs, start=1)]
    return result


def _merge_broken_headings(lines: list[str], headings: set[str]) -> list[str]:
    """Junta títulos do sumário que o HTML quebrou em duas ou mais linhas ("Evolução recente da" + "economia")."""
    merged: list[str] = []
    i = 0
    while i < len(lines):
        line, j = lines[i], i + 1
        while _key(line) not in headings and j < len(lines) and any(h.startswith(_key(line) + " ") for h in headings):
            line, j = f"{line} {lines[j]}", j + 1
        if _key(line) in headings:
            merged.append(line)
            i = j
        else:
            merged.append(lines[i])
            i += 1
    return merged


def _continues(previous: str, line: str) -> bool:
    """A linha continua o parágrafo anterior (HTML que quebra frases no meio)?"""
    if previous.endswith((".", "!", "?", ";", ":")):
        return False
    return line[:1].islower() or line[:1] in ",;)–-" or len(previous) >= 50


def _looks_like_roster(text: str) -> bool:
    return text.count(" – ") >= 3 or text.startswith(("Data:", "Local:", "Presentes"))


def _split_long(text: str) -> list[str]:
    """Quebra parágrafos muito longos em pedaços de até MAX_CHARS, respeitando o fim das frases."""
    if len(text) <= MAX_CHARS:
        return [text]
    sentences = re.split(r"(?<=[.;:])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9])", text)
    chunks, current = [], ""
    for sentence in sentences:
        if current and len(current) + len(sentence) > MAX_CHARS:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def _drop_empty_preamble(paragraphs: list[Paragraph]) -> list[Paragraph]:
    # Junta os pedaços do preâmbulo (data, local, lista de presentes) num único bloco.
    preamble = [p for p in paragraphs if p.number == 0]
    body = [p for p in paragraphs if p.number > 0]
    if not preamble:
        return body
    joined = " ".join(p.text for p in preamble)[: MAX_CHARS * 2]
    return [Paragraph(0, "Identificação", joined), *body]


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text.replace("\xa0", " "))
    return re.sub(r"\s+", " ", text).strip()


def _key(text: str) -> str:
    return _clean(text).lower().rstrip(":. ")
