"""Conectores para as bases de patentes consultadas pelo agente.

Cada função devolve um dicionário serializável em JSON com, no mínimo:
  - "fonte": nome da base
  - "url_consulta": link da mesma busca na interface web da base (serve para
    conferência humana e como alvo do web_fetch se a consulta direta falhar)
  - "resultados" (lista) ou "erro" (texto)

Nenhuma destas bases oferece uma API pública estável e gratuita para busca por
inventor, exceto o Espacenet (via EPO OPS, que exige cadastro gratuito). Os
demais conectores leem as páginas públicas e por isso podem quebrar se o site
mudar de layout; nesse caso o agente recorre ao web_fetch/web_search.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import unicodedata
from urllib.parse import quote, quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TIMEOUT = 40
MAX_TEXTO = 12000


def _sessao() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"})
    return s


def _limpar_html(texto: str | None) -> str:
    if not texto:
        return ""
    return re.sub(r"\s+", " ", BeautifulSoup(texto, "html.parser").get_text(" ")).strip()


def _texto_pagina(html: str, limite: int = MAX_TEXTO) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    texto = re.sub(r"\n\s*\n+", "\n", soup.get_text("\n"))
    texto = re.sub(r"[ \t]+", " ", texto).strip()
    return texto[:limite] + ("\n[...texto truncado...]" if len(texto) > limite else "")


def sem_acentos(nome: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", nome) if not unicodedata.combining(c)
    )


def _erro(fonte: str, url: str, e: Exception | str) -> dict:
    return {
        "fonte": fonte,
        "url_consulta": url,
        "erro": f"{type(e).__name__}: {e}" if isinstance(e, Exception) else e,
        "sugestao": "Tente web_fetch na url_consulta ou web_search restrito ao domínio da base.",
    }


# ---------------------------------------------------------------------------
# Google Patents
# ---------------------------------------------------------------------------

def buscar_google_patents(nome: str, termos_extras: str = "") -> dict:
    """Busca por inventor no Google Patents (endpoint JSON usado pelo próprio site)."""
    consulta = f"inventor={nome}&num=100"
    if termos_extras:
        consulta += f"&q={termos_extras}"
    url_web = f"https://patents.google.com/?inventor={quote_plus(nome)}" + (
        f"&q={quote_plus(termos_extras)}" if termos_extras else ""
    )
    url_api = f"https://patents.google.com/xhr/query?url={quote(consulta)}&exp="
    try:
        r = _sessao().get(url_api, timeout=TIMEOUT)
        r.raise_for_status()
        dados = r.json()
    except Exception as e:
        return _erro("Google Patents", url_web, e)

    res = dados.get("results", {})
    itens = []
    for cluster in res.get("cluster", []):
        for item in cluster.get("result", []):
            p = item.get("patent", {})
            itens.append(
                {
                    "numero_publicacao": p.get("publication_number"),
                    "titulo": _limpar_html(p.get("title")),
                    "inventores": _limpar_html(p.get("inventor")),
                    "titular": _limpar_html(p.get("assignee")),
                    "data_prioridade": p.get("priority_date"),
                    "data_deposito": p.get("filing_date"),
                    "data_publicacao": p.get("publication_date"),
                    "data_concessao": p.get("grant_date"),
                    "link": f"https://patents.google.com/{item.get('id', '')}",
                }
            )
    return {
        "fonte": "Google Patents",
        "url_consulta": url_web,
        "total_encontrado": res.get("total_num_results", len(itens)),
        "resultados": itens,
        "observacao": "A lista não traz o status legal; use detalhar_google_patents.",
    }


_ITEMPROPS_GP = {
    "status", "inventor", "assigneeCurrent", "assigneeOriginal",
    "priorityDate", "filingDate", "publicationDate", "applicationNumber",
}


def detalhar_google_patents(numero_publicacao: str) -> dict:
    """Lê a página de um documento no Google Patents: status, eventos e expiração."""
    num = re.sub(r"[\s\-/.,]", "", numero_publicacao.upper())
    url = f"https://patents.google.com/patent/{num}/pt"
    try:
        r = _sessao().get(url, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        return _erro("Google Patents", url, e)

    soup = BeautifulSoup(r.text, "html.parser")
    campos: dict[str, list[str]] = {}
    for el in soup.select("[itemprop]"):
        prop = el.get("itemprop")
        if prop in _ITEMPROPS_GP and not el.find("[itemprop]"):
            valor = el.get("datetime") or el.get_text(" ", strip=True)
            if valor and valor not in campos.setdefault(prop, []):
                campos[prop].append(valor)

    meta_titulo = soup.select_one('meta[name="DC.title"]')
    if meta_titulo and meta_titulo.get("content"):
        campos["titulo"] = [meta_titulo["content"].strip()]

    eventos = []
    for ev in soup.select('[itemprop="events"]'):
        data = ev.select_one('[itemprop="date"]')
        titulo = ev.select_one('[itemprop="title"]')
        if titulo:
            eventos.append(
                {
                    "data": (data.get("datetime") or data.get_text(strip=True)) if data else None,
                    "evento": titulo.get_text(" ", strip=True),
                }
            )

    eventos_legais = []
    for tr in soup.select('[itemprop="legalEvents"]'):
        cel = {
            td.get("itemprop"): td.get_text(" ", strip=True)
            for td in tr.select("[itemprop]")
        }
        if cel:
            eventos_legais.append(cel)

    return {
        "fonte": "Google Patents",
        "url_consulta": url,
        "numero_publicacao": num,
        "campos": campos,
        "linha_do_tempo": eventos,
        "eventos_legais": eventos_legais[-30:],
    }


# ---------------------------------------------------------------------------
# Espacenet (EPO Open Patent Services)
# ---------------------------------------------------------------------------

OPS = "https://ops.epo.org/3.2"
_token_cache: dict[str, float | str] = {}


def _ops_token() -> str | None:
    chave, segredo = os.getenv("EPO_OPS_KEY"), os.getenv("EPO_OPS_SECRET")
    if not chave or not segredo:
        return None
    if _token_cache.get("expira", 0) > time.time():
        return str(_token_cache["token"])
    cred = base64.b64encode(f"{chave}:{segredo}".encode()).decode()
    r = requests.post(
        f"{OPS}/auth/accesstoken",
        headers={"Authorization": f"Basic {cred}"},
        data={"grant_type": "client_credentials"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    d = r.json()
    _token_cache.update(token=d["access_token"], expira=time.time() + int(d.get("expires_in", 1200)) - 60)
    return d["access_token"]


def _lista(x) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _txt(x) -> str:
    if isinstance(x, dict):
        return str(x.get("$", ""))
    return "" if x is None else str(x)


def _ops_nomes(grupo: dict | None, chave: str, subnome: str) -> list[str]:
    """Nomes como grafados no documento original (ou no formato EPO, se não houver)."""
    pessoas = _lista((grupo or {}).get(chave))
    originais = [p for p in pessoas if p.get("@data-format") == "original"] or pessoas
    nomes: list[str] = []
    for p in originais:
        n = _txt(p.get(subnome, {}).get("name")).strip(" ,")
        if n and n not in nomes:
            nomes.append(n)
    return nomes


def _ops_datas(ref: dict | None) -> tuple[str, str]:
    for d in _lista((ref or {}).get("document-id")):
        if d.get("@document-id-type") == "docdb":
            num = f"{_txt(d.get('country'))}{_txt(d.get('doc-number'))}{_txt(d.get('kind'))}"
            return num, _txt(d.get("date"))
    return "", ""


def buscar_espacenet(nome: str) -> dict:
    """Busca por inventor no Espacenet via EPO OPS (requer EPO_OPS_KEY/SECRET)."""
    cql = f'in="{nome}"'
    url_web = f"https://worldwide.espacenet.com/patent/search?q={quote(cql)}"
    try:
        token = _ops_token()
    except Exception as e:
        return _erro("Espacenet", url_web, e)
    if not token:
        return _erro(
            "Espacenet",
            url_web,
            "Credenciais EPO_OPS_KEY/EPO_OPS_SECRET não configuradas (cadastro gratuito em "
            "https://developers.epo.org). Sem elas, consulte via web_search/web_fetch.",
        )

    itens, total = [], 0
    try:
        for inicio in (1, 101):
            r = requests.get(
                f"{OPS}/rest-services/published-data/search/biblio",
                params={"q": cql},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "X-OPS-Range": f"{inicio}-{inicio + 99}",
                },
                timeout=TIMEOUT,
            )
            if r.status_code == 404:  # OPS responde 404 quando não há resultados
                break
            r.raise_for_status()
            busca = r.json()["ops:world-patent-data"]["ops:biblio-search"]
            total = int(busca.get("@total-result-count", 0))
            docs = _lista(busca.get("ops:search-result", {}).get("exchange-documents"))
            for bloco in docs:
                for doc in _lista(bloco.get("exchange-document")):
                    bib = doc.get("bibliographic-data", {})
                    titulos = _lista(bib.get("invention-title"))
                    titulo = next(
                        (_txt(t) for t in titulos if t.get("@lang") == "pt"),
                        next((_txt(t) for t in titulos if t.get("@lang") == "en"), _txt(titulos[0]) if titulos else ""),
                    )
                    partes = bib.get("parties", {})
                    pub_num, pub_data = _ops_datas(bib.get("publication-reference"))
                    _, dep_data = _ops_datas(bib.get("application-reference"))
                    itens.append(
                        {
                            "numero_publicacao": pub_num
                            or f"{doc.get('@country')}{doc.get('@doc-number')}{doc.get('@kind')}",
                            "familia": doc.get("@family-id"),
                            "titulo": titulo,
                            "inventores": _ops_nomes(partes.get("inventors"), "inventor", "inventor-name"),
                            "titulares": _ops_nomes(partes.get("applicants"), "applicant", "applicant-name"),
                            "data_publicacao": pub_data,
                            "data_deposito": dep_data,
                        }
                    )
            if total <= inicio + 99:
                break
    except Exception as e:
        return _erro("Espacenet", url_web, e)

    return {
        "fonte": "Espacenet (EPO OPS)",
        "url_consulta": url_web,
        "total_encontrado": total,
        "resultados": itens,
        "observacao": "Use status_legal_espacenet para os eventos INPADOC de cada documento.",
    }


def status_legal_espacenet(numero_publicacao: str) -> dict:
    """Eventos legais INPADOC de um documento (ex.: BR102019012345A2)."""
    num = re.sub(r"[\s\-/.,]", "", numero_publicacao.upper())
    url_web = f"https://worldwide.espacenet.com/patent/search?q=pn%3D{num}"
    m = re.match(r"^([A-Z]{2})(\d+[A-Z]?\d*?)([A-Z]\d?)?$", num)
    if not m:
        return _erro("Espacenet", url_web, f"Número em formato não reconhecido: {numero_publicacao}")
    docdb = ".".join(p for p in m.groups() if p)
    try:
        token = _ops_token()
        if not token:
            return _erro("Espacenet", url_web, "Credenciais EPO OPS não configuradas.")
        r = requests.get(
            f"{OPS}/rest-services/legal/publication/docdb/{docdb}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        dados = r.json()
    except Exception as e:
        return _erro("Espacenet", url_web, e)

    eventos = []

    def coletar(no):
        if isinstance(no, dict):
            if "@code" in no and "@desc" in no:
                data = next(
                    (v for v in (_txt(x) for x in no.values()) if re.fullmatch(r"\d{8}", v)),
                    "",
                )
                eventos.append({"codigo": no["@code"], "descricao": no["@desc"], "data": data})
            for v in no.values():
                coletar(v)
        elif isinstance(no, list):
            for v in no:
                coletar(v)

    coletar(dados)
    return {
        "fonte": "Espacenet (INPADOC)",
        "url_consulta": url_web,
        "numero_publicacao": num,
        "eventos": eventos[-40:],
    }


# ---------------------------------------------------------------------------
# PATENTSCOPE (OMPI/WIPO)
# ---------------------------------------------------------------------------

def buscar_patentscope(nome: str) -> dict:
    """Busca por inventor (campo IN) na página pública de resultados do PATENTSCOPE."""
    consulta = f'IN:("{nome}")'
    url = f"https://patentscope.wipo.int/search/pt/result.jsf?query={quote(consulta)}"
    try:
        r = _sessao().get(url, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        return _erro("PATENTSCOPE", url, e)

    soup = BeautifulSoup(r.text, "html.parser")
    itens, vistos = [], set()
    for a in soup.select('a[href*="detail.jsf?docId="]'):
        doc_id = re.search(r"docId=([^&]+)", a["href"]).group(1)
        if doc_id in vistos:
            continue
        vistos.add(doc_id)
        bloco = a
        for _ in range(4):  # sobe até o contêiner do resultado
            if bloco.parent is None:
                break
            bloco = bloco.parent
            if len(bloco.get_text(" ", strip=True)) > 200:
                break
        itens.append(
            {
                "doc_id": doc_id,
                "resumo_do_resultado": re.sub(r"\s+", " ", bloco.get_text(" ", strip=True))[:700],
                "link": urljoin(url, a["href"]),
            }
        )

    total = re.search(r"([\d.,]+)\s+(?:resultados|results)", soup.get_text(" "))
    saida = {
        "fonte": "PATENTSCOPE",
        "url_consulta": url,
        "total_encontrado": total.group(1) if total else len(itens),
        "resultados": itens,
    }
    if not itens:
        saida["texto_pagina"] = _texto_pagina(r.text, 6000)
        saida["observacao"] = (
            "Nenhum resultado estruturado extraído (pode ser ausência de resultados ou página "
            "dinâmica). Confira o texto_pagina ou use web_fetch na url_consulta."
        )
    return saida


# ---------------------------------------------------------------------------
# INPI (pePI — Brasil)
# ---------------------------------------------------------------------------

PEPI = "https://busca.inpi.gov.br/pePI"


def _inpi_sessao() -> requests.Session:
    s = _sessao()
    s.get(f"{PEPI}/servlet/LoginController?action=login", timeout=TIMEOUT)  # acesso anônimo
    return s


def _campos_formulario(form) -> dict[str, str]:
    campos: dict[str, str] = {}
    for el in form.select("input[name], select[name], textarea[name]"):
        nome, tipo = el["name"], (el.get("type") or "").lower()
        if tipo in ("checkbox", "radio") and not el.has_attr("checked"):
            continue
        if tipo in ("submit", "button", "image", "reset") and nome in campos:
            continue
        if el.name == "select":
            op = el.select_one("option[selected]") or el.select_one("option")
            campos[nome] = op.get("value", op.get_text(strip=True)) if op else ""
        else:
            campos.setdefault(nome, el.get("value", ""))
    return campos


def buscar_inpi(nome: str) -> dict:
    """Pesquisa avançada de patentes do INPI (pePI) pelo nome do inventor."""
    url_web = f"{PEPI}/jsp/patentes/PatenteSearchAvancado.jsp"
    try:
        s = _inpi_sessao()
        r = s.get(url_web, timeout=TIMEOUT)
        r.encoding = "ISO-8859-1"
        soup = BeautifulSoup(r.text, "html.parser")
        form = next(
            (f for f in soup.find_all("form") if f.find(attrs={"name": re.compile("inventor", re.I)})),
            None,
        )
        if form:
            campos = _campos_formulario(form)
            campo_inv = next(
                n for n in campos if re.search("inventor", n, re.I) and not re.search("cpf|cnpj", n, re.I)
            )
            acao = urljoin(r.url, form.get("action") or "../../servlet/PatenteServletController")
        else:  # layout desconhecido: usa os nomes historicamente usados pelo pePI
            campos = {"Action": "SearchAvancado", "botao": " pesquisar » "}
            campo_inv = "NomeInventor"
            acao = f"{PEPI}/servlet/PatenteServletController"
        campos[campo_inv] = sem_acentos(nome)
        for chave in list(campos):
            if re.fullmatch(r"RegisterPerPage", chave, re.I):
                campos[chave] = "100"
        res = s.post(acao, data=campos, timeout=TIMEOUT)
        res.encoding = "ISO-8859-1"
    except Exception as e:
        return _erro("INPI (pePI)", url_web, e)

    soup = BeautifulSoup(res.text, "html.parser")
    itens, vistos = [], set()
    for a in soup.select('a[href*="Action=detail"]'):
        href = urljoin(res.url, a["href"])
        if href in vistos:
            continue
        vistos.add(href)
        tr = a.find_parent("tr")
        celulas = [c.get_text(" ", strip=True) for c in tr.find_all("td")] if tr else [a.get_text(strip=True)]
        celulas = [c for c in celulas if c]
        itens.append(
            {
                "numero_pedido": celulas[0] if celulas else a.get_text(strip=True),
                "colunas": celulas,  # normalmente: nº do pedido, data do depósito, título, IPC
                "link_detalhe": href,
            }
        )

    texto = soup.get_text(" ")
    total = re.search(r"Foram encontrados\s+(\d+)\s+processos", texto)
    saida = {
        "fonte": "INPI (pePI)",
        "url_consulta": url_web,
        "campo_utilizado": campo_inv,
        "total_encontrado": int(total.group(1)) if total else len(itens),
        "resultados": itens,
        "observacao": "O INPI grava nomes sem padronização; use detalhar_inpi para despachos/situação.",
    }
    if not itens:
        saida["texto_pagina"] = _texto_pagina(res.text, 4000)
    return saida


def detalhar_inpi(link_detalhe: str) -> dict:
    """Abre a ficha de um processo no pePI (dados, inventores e despachos da RPI)."""
    if not link_detalhe.startswith("http"):
        cod = re.sub(r"\D", "", link_detalhe)
        link_detalhe = f"{PEPI}/servlet/PatenteServletController?Action=detail&CodPedido={cod}"
    try:
        s = _inpi_sessao()
        r = s.get(link_detalhe, timeout=TIMEOUT)
        r.encoding = "ISO-8859-1"
        r.raise_for_status()
    except Exception as e:
        return _erro("INPI (pePI)", link_detalhe, e)
    return {
        "fonte": "INPI (pePI)",
        "url_consulta": link_detalhe,
        "texto_ficha": _texto_pagina(r.text),
    }


# Registro usado pelo agente: nome da ferramenta -> função
FUNCOES = {
    "buscar_google_patents": buscar_google_patents,
    "detalhar_google_patents": detalhar_google_patents,
    "buscar_espacenet": buscar_espacenet,
    "status_legal_espacenet": status_legal_espacenet,
    "buscar_patentscope": buscar_patentscope,
    "buscar_inpi": buscar_inpi,
    "detalhar_inpi": detalhar_inpi,
}


def executar(nome_ferramenta: str, argumentos: dict) -> tuple[str, bool]:
    """Roda a ferramenta e devolve (JSON do resultado, houve_erro)."""
    try:
        resultado = FUNCOES[nome_ferramenta](**argumentos)
    except Exception as e:  # nunca derruba o agente por falha de uma base
        resultado = {"erro": f"{type(e).__name__}: {e}"}
    return json.dumps(resultado, ensure_ascii=False), "erro" in resultado
