#!/bin/bash
# Agente de Patentes - de dois cliques neste arquivo (Mac)
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo
  echo " O Python nao esta instalado neste computador."
  echo " Acesse https://www.python.org/downloads/ , instale e abra este arquivo de novo."
  echo
  read -p "Pressione Enter para fechar..."
  exit 1
fi

if [ ! -f "$HOME/.streamlit/credentials.toml" ]; then
  mkdir -p "$HOME/.streamlit"
  printf '[general]\nemail = ""\n' > "$HOME/.streamlit/credentials.toml"
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Preparando o programa pela primeira vez. Isso leva alguns minutos..."
  python3 -m venv .venv
fi
echo "Verificando componentes..."
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
echo
echo " O programa vai abrir no seu navegador."
echo " NAO feche esta janela enquanto estiver usando."
echo
.venv/bin/python -m streamlit run app.py --browser.gatherUsageStats false
