@echo off
cd /d "%~dp0"
start "" pythonw audio_mixer.py %*
