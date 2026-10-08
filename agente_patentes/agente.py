"""Agente de busca de patentes e modelos de utilidade por nome de pesquisador.

Uso:
    python agente.py "Nome do Pesquisador"
    python agente.py "Nome do Pesquisador" --instituicao "UFMG" --saida relatorio.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import anthropic

import fontes


def _carregar_env(caminho: str = os.path.join(os.path.dirname(__file__), ".env")) -> None:
    """Lê chaves do arquivo .env (se existir) sem sobrescrever variáveis já definidas."""
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            for linha in f:
                chave, sep, valor = linha.strip().partition("=")
                if sep and not chave.startswith("#") and valor:
                    os.environ.setdefault(chave.strip(), valor.strip())


_carregar_env()

MODELO = "claude-opus-5-5"
MAX_RODADAS = 40

SISTEMA = """Você é um analista de propriedade intelectual especializado em patentes brasileiras e \
internacionais. Sua tarefa: dado o nome de um pesquisador, descobrir se ele consta como inventor em \
pedidos de patente de invenção (PI) ou modelos de utilidade (MU) e qual a situação de cada um.

## Bases obrigatórias
Consulte as quatro bases: Espacenet, PATENTSCOPE, Google Patents e INPI. Use primeiro as ferramentas \
diretas (buscar_*). Se uma ferramenta devolver erro ou resultado vazio suspeito, recorra ao web_fetch \
na `url_consulta` retornada e/ou ao web_search restrito ao domínio da base (ex.: \
"site:busca.inpi.gov.br", "site:patentscope.wipo.int"). Registre no relatório quais bases foram \
efetivamente consultadas e por qual via; nunca afirme que algo não existe numa base que não conseguiu \
consultar.

## Variações de nome
Bases gravam nomes de formas diferentes (sem acento, MAIÚSCULAS, "SOBRENOME, NOME", abreviações, \
sobrenome de casada). Faça buscas com 2 a 4 variações razoáveis, começando pela forma completa sem \
acentos e pela forma "nome + último sobrenome".

## Homônimos
Para cada documento, avalie se o inventor é de fato o pesquisador (coinventores recorrentes, titular \
ser a instituição informada, área técnica compatível, país). Classifique a confiança: Alta / Média / \
Baixa. Documentos de baixa confiança vão para uma seção separada.

## Família de patentes
O mesmo invento aparece em várias bases e países (BR, WO, US, EP...). Agrupe por invento/família e \
liste os números correspondentes, em vez de repetir linhas.

## Tipo de proteção
- INPI, numeração atual: BR 10 / BR 11 (fase nacional PCT) = patente de invenção; BR 20 / BR 21 = \
modelo de utilidade; BR 12 / BR 13 = certificado de adição. Numeração antiga: PI = invenção, \
MU = modelo de utilidade, C1/C2... = certificado de adição.
- Códigos de tipo de documento (kind): no BR, A2/A8 = pedido publicado, B1/B8 = patente concedida, \
U2 = MU publicado, Y1 = MU concedido. WO = pedido internacional PCT (não é patente concedida).

## Situação (use exatamente uma destas categorias)
- Solicitada (pedido em andamento / publicado, sem decisão)
- Concedida – vigente
- Concedida – expirada (fim do prazo de vigência)
- Extinta / caducada (ex.: falta de pagamento de anuidade, renúncia)
- Arquivada / retirada
- Indeferida
- Desconhecida (sem evidência suficiente)
Prazos no Brasil (Lei 9.279/96, art. 40): invenção 20 anos e MU 15 anos contados do depósito \
(após a ADI 5529/2021 não há mais o prazo mínimo contado da concessão para a maioria dos casos). \
Para o INPI, a situação sai dos despachos da RPI na ficha do processo (ex.: 16.1 concessão; 9.1 \
deferimento; 9.2 indeferimento; 11.x arquivamento; 21.x extinção) — confira a descrição do \
despacho, não apenas o código. Para outros países, use o status legal do Google Patents e os eventos \
INPADOC do Espacenet. Para decidir se está vigente/expirada, compare com a data de hoje.

Abra detalhes (detalhar_*, status_legal_espacenet) apenas dos documentos com confiança Alta ou Média; \
se houver muitos, priorize um por família.

## Relatório final (Markdown, em português)
1. **Resumo**: o pesquisador possui patentes/MU? Quantos inventos (famílias), quantos concedidos e \
vigentes, quantos pedidos em andamento.
2. **Tabela principal** (confiança Alta/Média) com colunas: Invento (título) | Tipo (PI/MU/PCT/outro) \
| Números (por país) | Titular | Depósito | Situação | Evidência da situação | Bases onde aparece | \
Confiança | Link.
3. **Possíveis homônimos** (confiança Baixa), em lista curta.
4. **Cobertura da busca**: para cada base, variações de nome usadas, via (ferramenta direta, web_fetch \
ou web_search), total de resultados e falhas.
5. **Ressalvas**: lembre que a busca não substitui um parecer de busca de anterioridade e que status \
legal deve ser confirmado na base oficial antes de uso formal.
Não invente números, datas ou situações: se não encontrou a informação, escreva "não informado".
"""


def _ferramenta(nome: str, descricao: str, props: dict, obrigatorios: list[str]) -> dict:
    return {
        "name": nome,
        "description": descricao,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": props,
            "required": obrigatorios,
            "additionalProperties": False,
        },
    }


_NOME = {"nome": {"type": "string", "description": "Nome (ou variação do nome) do inventor."}}
_NUM = {
    "numero_publicacao": {
        "type": "string",
        "description": "Número de publicação com país e código, ex.: BR102019012345A2, US2021123456A1.",
    }
}

FERRAMENTAS = [
    _ferramenta(
        "buscar_google_patents",
        "Busca documentos no Google Patents em que a pessoa aparece como inventora. Retorna número, "
        "título, inventores, titular e datas (sem status legal).",
        {
            **_NOME,
            "termos_extras": {
                "type": "string",
                "description": "Palavras-chave opcionais para filtrar homônimos (ex.: área ou instituição). "
                "Use string vazia se não houver.",
            },
        },
        ["nome", "termos_extras"],
    ),
    _ferramenta(
        "detalhar_google_patents",
        "Abre a página de um documento no Google Patents: status legal (Active/Expired/Pending...), "
        "expiração prevista, inventores, titular e eventos legais.",
        _NUM,
        ["numero_publicacao"],
    ),
    _ferramenta(
        "buscar_espacenet",
        "Busca por inventor no Espacenet (EPO OPS, base mundial com documentos do INPI e do PCT).",
        _NOME,
        ["nome"],
    ),
    _ferramenta(
        "status_legal_espacenet",
        "Eventos legais INPADOC (concessão, extinção, pagamento de taxas, etc.) de um documento no Espacenet.",
        _NUM,
        ["numero_publicacao"],
    ),
    _ferramenta(
        "buscar_patentscope",
        "Busca por inventor no PATENTSCOPE (OMPI), que cobre pedidos PCT (WO) e coleções nacionais.",
        _NOME,
        ["nome"],
    ),
    _ferramenta(
        "buscar_inpi",
        "Pesquisa avançada no INPI (pePI) pelo nome do inventor; retorna pedidos brasileiros de patente "
        "de invenção e modelo de utilidade com link para a ficha.",
        _NOME,
        ["nome"],
    ),
    _ferramenta(
        "detalhar_inpi",
        "Abre a ficha de um processo no INPI: dados do pedido, inventores, titular e despachos da RPI "
        "(de onde sai a situação: concedida, arquivada, extinta...).",
        {"link_detalhe": {"type": "string", "description": "link_detalhe retornado por buscar_inpi."}},
        ["link_detalhe"],
    ),
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 15},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 20},
]


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def pesquisar(nome: str, instituicao: str = "", area: str = "", log=_log) -> str:
    """Executa o agente e devolve o relatório final em Markdown."""
    cliente = anthropic.Anthropic()

    pedido = f"Pesquisador: {nome}\nData de hoje: {date.today().isoformat()}"
    if instituicao:
        pedido += f"\nInstituição/afiliação conhecida: {instituicao}"
    if area:
        pedido += f"\nÁrea de atuação: {area}"
    pedido += "\n\nFaça a busca nas quatro bases e entregue o relatório."
    mensagens: list = [{"role": "user", "content": pedido}]

    for _ in range(MAX_RODADAS):
        resposta = cliente.beta.messages.create(
            model=MODELO,
            max_tokens=16000,
            system=SISTEMA,
            tools=FERRAMENTAS,
            messages=mensagens,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        mensagens.append({"role": "assistant", "content": resposta.content})

        for bloco in resposta.content:
            if bloco.type == "server_tool_use":
                log(f"  · {bloco.name}: {json.dumps(bloco.input, ensure_ascii=False)[:150]}")

        if resposta.stop_reason == "refusal":
            raise RuntimeError("O modelo recusou a solicitação.")
        if resposta.stop_reason == "pause_turn":  # ferramentas de servidor ainda trabalhando
            continue
        if resposta.stop_reason == "max_tokens":
            raise RuntimeError("Resposta truncada (max_tokens); tente novamente.")
        if resposta.stop_reason != "tool_use":
            return "\n".join(b.text for b in resposta.content if b.type == "text").strip()

        chamadas = [b for b in resposta.content if b.type == "tool_use"]
        for c in chamadas:
            log(f"  · {c.name}: {json.dumps(c.input, ensure_ascii=False)}")
        with ThreadPoolExecutor(max_workers=6) as pool:
            saidas = list(pool.map(lambda c: fontes.executar(c.name, c.input), chamadas))
        mensagens.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": c.id,
                        "content": conteudo,
                        "is_error": erro,
                    }
                    for c, (conteudo, erro) in zip(chamadas, saidas)
                ],
            }
        )

    raise RuntimeError(f"O agente não concluiu em {MAX_RODADAS} rodadas.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Busca patentes e modelos de utilidade de um pesquisador.")
    ap.add_argument("nome", help="Nome completo do pesquisador")
    ap.add_argument("--instituicao", default="", help="Instituição/afiliação (ajuda a separar homônimos)")
    ap.add_argument("--area", default="", help="Área de atuação (ajuda a separar homônimos)")
    ap.add_argument("--saida", help="Arquivo .md onde salvar o relatório")
    args = ap.parse_args()

    _log(f"Pesquisando patentes de {args.nome}...")
    try:
        relatorio = pesquisar(args.nome, args.instituicao, args.area)
    except anthropic.AuthenticationError:
        sys.exit("Erro: defina a variável ANTHROPIC_API_KEY com uma chave válida.")
    except anthropic.RateLimitError:
        sys.exit("Erro: limite de requisições da API atingido; tente novamente em instantes.")
    except anthropic.APIStatusError as e:
        sys.exit(f"Erro da API ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        sys.exit("Erro: sem conexão com a API da Anthropic.")
    except RuntimeError as e:
        sys.exit(f"Erro: {e}")

    print(relatorio)
    if args.saida:
        with open(args.saida, "w", encoding="utf-8") as f:
            f.write(relatorio + "\n")
        _log(f"Relatório salvo em {args.saida}")


if __name__ == "__main__":
    main()
