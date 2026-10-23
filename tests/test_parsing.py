from copom_mcp.parsing import html_to_lines, segment

RECENT = """
<div id="atacompleta"><h3 class="secao">A) Atualização da conjuntura<sup>1</sup></h3>
<p class="paragrafo">1. O ambiente externo permanece incerto em função dos <em>conflitos</em> no Oriente Médio.</p>
<p class="paragrafo">2. A atividade econômica doméstica manteve trajetória de moderação, como antecipado.</p>
<table><tr><td>Projeções</td><td>4,1%</td></tr></table>
<h3 class="secao">B) Riscos</h3>
<p class="paragrafo">3. Os riscos para a inflação permanecem mais elevados que o usual, com assimetria altista.</p>
</div>
"""

# Formato de ~2015: número do parágrafo sozinho na linha e título quebrado em duas linhas.
WRAPPED = """
<div><a href="#t1">Evolução recente da economia</a></div>
<div>Data: 28 e 29/4/2015</div>
<div>Evolução recente da</div><div>economia</div>
<div>1.</div><div>A inflação medida pelo IPCA foi de 1,32% em março, acima da registrada</div>
<div>no mês anterior, refletindo reajustes de preços administrados.</div>
<div>2.</div><div>Os preços livres desaceleraram, com destaque para alimentos e bens industriais no período.</div>
<div>3. O mercado de trabalho mostrou sinais de acomodação, com alta gradual da taxa de desemprego.</div>
<div>4. O crédito desacelerou, em linha com a política monetária mais restritiva adotada recentemente.</div>
<div>5. As expectativas de inflação para os próximos anos permaneceram acima da meta de 4,5%.</div>
"""

# Atas antigas: sem numeração, com frases quebradas pelo HTML do Word.
OLD = """
<a href="#a1">Atividade econômica</a><a href="#a2">Preços</a>
<p>Data: 23.5.2001</p><p>Presentes: Armínio Fraga – Presidente</p>
<p>Atividade econômica</p>
<p>Indicadores do nível de atividade, divulgados recentemente, mostraram que mesmo antes da crise</p>
<p>energética o ritmo de crescimento da economia já se mostrava moderado.</p>
<p>No comércio varejista, o faturamento real na Região Metropolitana de São Paulo recuou em abril.</p>
<p>Preços</p>
<p>O IPCA registrou variação de 0,58% em abril, impulsionado pelos preços administrados no período.</p>
"""


def parse(html: str):
    lines, toc = html_to_lines(html)
    return segment(lines, toc)


def test_recent_format_with_sections_and_numbers():
    paragraphs = [p for p in parse(RECENT) if p.number]
    assert [(p.number, p.section) for p in paragraphs] == [
        (1, "A) Atualização da conjuntura"),
        (2, "A) Atualização da conjuntura"),
        (3, "B) Riscos"),
    ]
    # Itálico não quebra o texto, e notas de rodapé e tabelas ficam de fora.
    assert "dos conflitos no Oriente" in paragraphs[0].text
    assert "Projeções" not in " ".join(p.text for p in paragraphs)


def test_lone_numbers_and_broken_headings():
    paragraphs = [p for p in parse(WRAPPED) if p.number]
    assert [p.number for p in paragraphs] == [1, 2, 3, 4, 5]
    assert paragraphs[0].section == "Evolução recente da economia"
    assert paragraphs[0].text.endswith("refletindo reajustes de preços administrados.")


def test_old_unnumbered_format_falls_back_to_blocks():
    result = parse(OLD)
    preamble, body = result[0], result[1:]
    assert preamble.number == 0 and "Armínio Fraga" in preamble.text
    assert [(p.number, p.section) for p in body] == [
        (1, "Atividade econômica"),
        (2, "Atividade econômica"),
        (3, "Preços"),
    ]
    assert "mesmo antes da crise energética o ritmo" in body[0].text


# Atas recentes: notas de rodapé e lista de presentes vêm depois do último parágrafo.
TRAILER = """
<h3>C) Discussão sobre a condução da política monetária</h3>
<p>15. O Comitê avalia que as evidências de transmissão da política monetária têm se acumulado.</p>
<p>16. Choques de oferta têm exercido influência relevante sobre a trajetória recente da inflação.</p>
<p>17. O Comitê reitera que a magnitude do ciclo será ajustada à luz da evolução do cenário.</p>
<h3>D) Decisão de política monetária</h3>
<p>18. O Copom decidiu reduzir a taxa básica de juros para 13,75% a.a., em linha com a estratégia.</p>
<p>19. Votaram por essa decisão os seguintes membros do Comitê: Fulano (presidente) e Beltrano.</p>
<p>Tabela 1 Projeções de inflação no cenário de referência</p>
<h3>Notas de rodapé</h3>
<p>1. A menos de menção explícita em contrário, esta atualização leva em conta as mudanças ocorridas.</p>
<h3>Presentes:</h3>
<p>Os membros do Copom analisaram a evolução recente e as perspectivas para a economia brasileira.</p>
"""


def test_trailing_footnotes_and_roster_are_not_paragraphs():
    result = parse(TRAILER)
    body = [p for p in result if p.number]
    assert [p.number for p in body] == [15, 16, 17, 18, 19]
    assert body[-1].text.endswith("Fulano (presidente) e Beltrano.")
    assert "A menos de menção" not in " ".join(p.text for p in result)
    assert result[0].number == 0 and "Os membros do Copom analisaram" in result[0].text


# Ata de 2002 sem numeração, com uma lista "1." a "4." no meio e texto longo depois dela (75ª reunião).
OLD_WITH_LIST = (
    "<a href='#a1'>Preços</a><a href='#a2'>Diretrizes</a><p>Data: 22.5.2002</p><p>Preços</p>"
    "<p>O IPCA registrou variação de 0,21% em abril, com alta concentrada nos preços administrados por contrato.</p>"
    "<p>1. Para todo 2002, projeta-se uma elevação de 3,3% no preço da gasolina ao consumidor final.</p>"
    "<p>2. Para o reajuste das tarifas de energia elétrica residencial, projeta-se alta de 16,5% no ano.</p>"
    "<p>3. Para o conjunto dos preços administrados por contrato, projeta-se variação de 9,2% em 2002.</p>"
    "<p>4. A projeção para o spread de seis meses sobre a taxa Selic segue o modelo de estrutura a termo.</p>"
    "<p>Diretrizes</p>"
    + "".join(f"<p>Parágrafo {i} sem número, com análise da conjuntura. {'Texto longo. ' * 150}</p>" for i in range(5))
)


def test_numbered_list_in_unnumbered_minutes_does_not_swallow_the_text():
    body = [p for p in parse(OLD_WITH_LIST) if p.number]
    assert [p.number for p in body] == list(range(1, len(body) + 1))  # modo de blocos: nenhum número repetido
    assert body[-1].section == "Diretrizes"


# Atas de 2009 colam o número no texto em alguns parágrafos.
GLUED = "".join(
    f"<div>{n}.{' ' if n != 3 else ''}Conforme a pesquisa, o indicador {n} variou 1.241 pontos no mês.</div>"
    for n in range(1, 6)
)


def test_number_glued_to_the_text():
    assert [p.number for p in parse(GLUED) if p.number] == [1, 2, 3, 4, 5]
