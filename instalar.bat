@echo off
chcp 65001 >nul
pushd "%~dp0"
echo Creando entorno virtual con Python 3.12...
py -3.12 -m venv .venv
if errorlevel 1 (
  echo.
  echo No encontre Python 3.12. Instalalo escribiendo en una terminal:  py install 3.12
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Fallo la instalacion de librerias.
  pause
  exit /b 1
)
echo Listo.
popd
