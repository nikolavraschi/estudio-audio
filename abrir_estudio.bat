@echo off
chcp 65001 >nul
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" call instalar.bat
if not exist ".venv\Scripts\python.exe" (popd & exit /b 1)
start "" ".venv\Scripts\pythonw.exe" estudio.py
popd
