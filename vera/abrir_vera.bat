@echo off
chcp 65001 >nul
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" call instalar.bat
".venv\Scripts\python.exe" app.py
if errorlevel 1 pause
popd
