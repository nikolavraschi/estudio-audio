@echo off
cd /d "%~dp0"
rem Usa el entorno compartido del estudio si existe; si no, el Python del sistema (como antes).
if exist "..\.venv\Scripts\pythonw.exe" (
  start "" "..\.venv\Scripts\pythonw.exe" audio_mixer.py %*
) else (
  start "" pythonw audio_mixer.py %*
)
