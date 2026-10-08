"""Interface web simples para o agente. Rode com:  streamlit run app.py"""

import streamlit as st

from agente import pesquisar

st.set_page_config(page_title="Patentes de pesquisadores", page_icon="📄", layout="wide")
st.title("Patentes e modelos de utilidade de pesquisadores")
st.caption("Busca em Espacenet, PATENTSCOPE, Google Patents e INPI.")

with st.form("busca"):
    nome = st.text_input("Nome do pesquisador")
    c1, c2 = st.columns(2)
    instituicao = c1.text_input("Instituição (opcional — ajuda a separar homônimos)")
    area = c2.text_input("Área de atuação (opcional)")
    enviar = st.form_submit_button("Pesquisar")

if enviar and nome.strip():
    passos = st.status("Consultando as bases...", expanded=True)
    try:
        relatorio = pesquisar(nome.strip(), instituicao.strip(), area.strip(), log=passos.write)
    except Exception as e:
        passos.update(label="Falha na busca", state="error")
        st.error(str(e))
    else:
        passos.update(label="Busca concluída", state="complete", expanded=False)
        st.markdown(relatorio)
        st.download_button(
            "Baixar relatório (.md)", relatorio, file_name=f"patentes_{nome.strip().replace(' ', '_')}.md"
        )
