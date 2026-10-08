"""Interface web simples para o agente. Rode com:  streamlit run app.py
(ou dê dois cliques em iniciar.bat no Windows / iniciar.command no Mac)."""

import os

import anthropic
import streamlit as st

from agente import pesquisar

ARQUIVO_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
CHAVES = ("ANTHROPIC_API_KEY", "EPO_OPS_KEY", "EPO_OPS_SECRET")


def salvar_chaves(novas: dict) -> None:
    """Grava as chaves no .env (ao lado deste arquivo) e já as ativa nesta sessão."""
    for k, v in novas.items():
        if v:
            os.environ[k] = v
    with open(ARQUIVO_ENV, "w", encoding="utf-8") as f:
        for k in CHAVES:
            f.write(f"{k}={os.environ.get(k, '')}\n")


st.set_page_config(page_title="Patentes de pesquisadores", page_icon="📄", layout="wide")
st.title("Patentes e modelos de utilidade de pesquisadores")
st.caption("Busca em Espacenet, PATENTSCOPE, Google Patents e INPI.")

tem_chave = os.getenv("ANTHROPIC_API_KEY", "").startswith("sk-ant-") and "..." not in os.getenv(
    "ANTHROPIC_API_KEY", ""
)

with st.expander("⚙️ Configurações (chaves de acesso)", expanded=not tem_chave):
    if not tem_chave:
        st.info(
            "Para começar, cole abaixo a sua chave da Anthropic. Ela começa com **sk-ant-** e é "
            "criada em https://console.anthropic.com/settings/keys . Você só precisa fazer isso uma vez."
        )
    with st.form("config"):
        chave = st.text_input("Chave da Anthropic (obrigatória)", type="password",
                              placeholder="sk-ant-..." if not tem_chave else "já configurada — cole outra para trocar")
        st.markdown(
            "Opcional — chaves gratuitas do Espacenet (https://developers.epo.org). "
            "Sem elas o Espacenet é pesquisado por busca na web."
        )
        epo_k = st.text_input("Espacenet: Consumer Key", type="password")
        epo_s = st.text_input("Espacenet: Consumer Secret", type="password")
        if st.form_submit_button("Salvar chaves"):
            chave = chave.strip()
            if chave and not chave.startswith("sk-ant-"):
                st.error("Essa chave não parece correta: ela deve começar com sk-ant-")
            else:
                salvar_chaves({"ANTHROPIC_API_KEY": chave, "EPO_OPS_KEY": epo_k.strip(),
                               "EPO_OPS_SECRET": epo_s.strip()})
                st.rerun()

if not tem_chave:
    st.stop()

with st.form("busca"):
    nome = st.text_input("Nome do pesquisador")
    c1, c2 = st.columns(2)
    instituicao = c1.text_input("Instituição (opcional — ajuda a separar homônimos)")
    area = c2.text_input("Área de atuação (opcional)")
    enviar = st.form_submit_button("Pesquisar")

if enviar and nome.strip():
    passos = st.status("Consultando as bases... (pode levar alguns minutos)", expanded=True)
    try:
        relatorio = pesquisar(nome.strip(), instituicao.strip(), area.strip(), log=passos.write)
    except anthropic.AuthenticationError:
        passos.update(label="Falha na busca", state="error")
        st.error("A chave da Anthropic foi recusada. Confira-a em Configurações.")
    except anthropic.PermissionDeniedError:
        passos.update(label="Falha na busca", state="error")
        st.error("A chave não tem permissão de uso. Verifique sua conta no console da Anthropic.")
    except anthropic.BadRequestError as e:
        passos.update(label="Falha na busca", state="error")
        if "credit" in str(e).lower():
            st.error("Sua conta da Anthropic está sem créditos. Adicione créditos em "
                     "https://console.anthropic.com/settings/billing")
        else:
            st.error(f"Erro na requisição: {e}")
    except anthropic.APIConnectionError:
        passos.update(label="Falha na busca", state="error")
        st.error("Sem conexão com a internet ou com a Anthropic. Tente novamente.")
    except Exception as e:
        passos.update(label="Falha na busca", state="error")
        st.error(str(e))
    else:
        passos.update(label="Busca concluída", state="complete", expanded=False)
        st.markdown(relatorio)
        st.download_button(
            "Baixar relatório (.md)", relatorio, file_name=f"patentes_{nome.strip().replace(' ', '_')}.md"
        )
