# Agente de busca de patentes por pesquisador

Agente de IA (Claude) que recebe o **nome de um pesquisador** e verifica se ele aparece como
inventor em **patentes de invenção** ou **modelos de utilidade**, informando a situação de cada
documento: **solicitada, concedida – vigente, concedida – expirada, extinta, arquivada, indeferida**.

Bases consultadas:

| Base | Como o agente consulta |
|---|---|
| **INPI (pePI)** | Pesquisa avançada pelo nome do inventor + leitura da ficha (despachos da RPI) |
| **Espacenet** | API oficial EPO OPS (busca `in=` + eventos legais INPADOC) |
| **Google Patents** | Busca por inventor + página do documento (status legal e expiração prevista) |
| **PATENTSCOPE** | Página pública de resultados (`IN:`) |

Se alguma consulta direta falhar (site fora do ar, mudança de layout, bloqueio), o agente usa
as ferramentas `web_search` e `web_fetch` do Claude como plano B, e o relatório diz qual via foi
usada em cada base.

O agente também:
- testa **variações do nome** (sem acento, "SOBRENOME, NOME", abreviações);
- separa **homônimos** atribuindo confiança Alta/Média/Baixa a cada documento (informe a
  instituição e a área para melhorar isso);
- agrupa o mesmo invento depositado em vários países (**família de patentes**: BR, WO, US, EP...);
- classifica o **tipo** (PI, MU, PCT, certificado de adição) pela numeração/código do documento.

## Instalação

Requer Python 3.10+.

```bash
cd agente_patentes
pip install -r requirements.txt
cp .env.example .env   # e preencha as chaves
```

No `.env`:
- `ANTHROPIC_API_KEY` — **obrigatória** ([console.anthropic.com](https://console.anthropic.com)).
- `EPO_OPS_KEY` / `EPO_OPS_SECRET` — opcionais, mas recomendadas: cadastro gratuito em
  [developers.epo.org](https://developers.epo.org) (crie um "app" e copie *Consumer Key* e
  *Consumer Secret*). Sem elas, o Espacenet é consultado só pelo plano B (web search).

## Uso

Linha de comando:

```bash
python agente.py "Maria da Silva Santos"
python agente.py "Maria da Silva Santos" --instituicao "UFMG" --area "química" --saida relatorio.md
```

O progresso (cada consulta feita) aparece no terminal e o relatório em Markdown é impresso no
final (e salvo, se usar `--saida`).

Interface web:

```bash
streamlit run app.py
```

Uso como biblioteca:

```python
from agente import pesquisar
relatorio = pesquisar("Maria da Silva Santos", instituicao="UFMG")
```

## O relatório

1. **Resumo** — se o pesquisador tem patentes/MU, quantos inventos, concedidos, vigentes e em andamento.
2. **Tabela principal** — invento, tipo, números por país, titular, depósito, situação,
   evidência da situação (ex.: despacho 16.1 na RPI, "Anticipated expiration 2039"), bases, confiança, link.
3. **Possíveis homônimos**.
4. **Cobertura da busca** — o que foi consultado em cada base e o que falhou.
5. **Ressalvas**.

## Limitações

- Google Patents, PATENTSCOPE e INPI não têm API pública gratuita de busca por inventor; os
  conectores leem as páginas públicas e podem parar de funcionar se os sites mudarem. Quando isso
  acontece o agente cai no plano B, mas vale ajustar `fontes.py`.
- A situação é inferida dos dados públicos de cada base; para uso formal (contratos, editais,
  Lattes), confirme na base oficial do respectivo escritório.
- Pedidos ficam em sigilo por 18 meses após o depósito e não aparecem em nenhuma base nesse período.
- Cada busca faz várias chamadas ao modelo; o custo de API cresce com o número de documentos
  encontrados. Acompanhe o consumo no console da Anthropic.

## Arquivos

- `agente.py` — instruções do agente, definição das ferramentas, loop com o Claude e CLI.
- `fontes.py` — conectores das quatro bases.
- `app.py` — interface web (Streamlit).
