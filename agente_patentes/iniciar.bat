@echo off
chcp 65001 >nul
title Agente de Patentes
cd /d "%~dp0"

set PY=
python --version >nul 2>nul && set PY=python
if "%PY%"=="" ( py --version >nul 2>nul && set PY=py )
if "%PY%"=="" (
  echo.
  echo  O Python nao esta instalado neste computador.
  echo  1. Acesse https://www.python.org/downloads/ e clique em "Download Python".
  echo  2. Na instalacao, MARQUE a opcao "Add python.exe to PATH".
  echo  3. Depois, de dois cliques neste arquivo de novo.
  echo.
  pause
  exit /b
)

if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
  mkdir "%USERPROFILE%\.streamlit" 2>nul
  (echo [general]& echo email = "")> "%USERPROFILE%\.streamlit\credentials.toml"
)

if not exist ".venv\Scripts\python.exe" (
  echo Preparando o programa pela primeira vez. Isso leva alguns minutos...
  %PY% -m venv .venv
)
echo Verificando componentes...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
echo.
echo  O programa vai abrir no seu navegador.
echo  NAO feche esta janela enquanto estiver usando. Para encerrar, feche esta janela.
echo.
".venv\Scripts\python.exe" -m streamlit run app.py --browser.gatherUsageStats false
pause
