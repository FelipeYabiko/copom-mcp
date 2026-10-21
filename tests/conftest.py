import pytest

from copom_mcp.corpus import Chunk, Meeting


@pytest.fixture
def meetings() -> list[Meeting]:
    return [
        Meeting(30, "1998-12-16", "1998-12-20", "30ª Reunião"),
        Meeting(279, "2026-06-17", "2026-06-23", "279ª Reunião", "https://bcb/279.pdf", 14.5, 14.25),
        Meeting(280, "2026-08-05", "2026-08-11", "280ª Reunião", "https://bcb/280.pdf", 14.25, 14.0),
        Meeting(281, "2026-09-16", "2026-09-22", "281ª Reunião", "https://bcb/281.pdf", 14.0, 14.0),
    ]


@pytest.fixture
def chunks() -> list[Chunk]:
    return [
        Chunk(30, "1998-12-16", "Identificação", 0, "Presentes: membros da diretoria."),
        Chunk(30, "1998-12-16", "Ambiente externo", 1, "A crise da Rússia elevou a aversão a risco nos mercados."),
        Chunk(
            279, "2026-06-17", "A) Conjuntura", 1, "O ambiente externo segue incerto com conflitos no Oriente Médio."
        ),
        Chunk(279, "2026-06-17", "B) Riscos", 2, "O preço do petróleo pressiona a inflação de combustíveis."),
        Chunk(
            280,
            "2026-08-05",
            "A) Conjuntura",
            1,
            "A inflação de serviços segue elevada e o mercado de trabalho, aquecido.",
        ),
        Chunk(281, "2026-09-16", "A) Conjuntura", 1, "As expectativas de inflação recuaram, mas seguem acima da meta."),
        Chunk(281, "2026-09-16", "C) Decisão", 2, "O Comitê decidiu manter a taxa Selic em 14,00% a.a."),
    ]
