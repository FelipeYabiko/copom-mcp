---
name: nova-serie
description: Adiciona um novo indicador do Banco Central (série do SGS) à ferramenta consultar_indicador. Use quando pedirem para incluir uma série como CDI, IPCA-15, desemprego ou câmbio de outra moeda.
argument-hint: "[nome do indicador ou código SGS]"
allowed-tools: Bash(curl *), Bash(pytest *)
---

# Adicionar uma série do SGS

1. Descubra o código da série. Se o usuário não informou, procure em https://www3.bcb.gov.br/sgspub/ ou pergunte.
2. Confirme que a série responde e veja a unidade e a periodicidade:

   ```bash
   curl -s "https://api.bcb.gov.br/dados/serie/bcdata.sgs.<CODIGO>/dados/ultimos/3?formato=json"
   ```

   Se vier vazio ou com erro, pare e avise: o código está errado ou a série foi descontinuada.
3. Em `src/copom_mcp/server.py`, adicione a entrada em `INDICATORS` (`"chave": (codigo, "Nome", "unidade")`) e
   a mesma chave no `Literal` de `Indicator`. Use chaves em minúsculas, sem acento, separadas por `_`.
4. Em `tests/test_server.py`, acrescente um teste no estilo de `test_indicator_uses_sgs` para a nova chave.
5. Rode `pytest -q` e mostre um exemplo real de resposta (o `curl` do passo 2) ao usuário.
6. Atualize a tabela de indicadores no README.
