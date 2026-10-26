@echo off
title Brixigo Connector
cd /d "%~dp0"
set QT_QPA_PLATFORM=
set QT_QPA_PLATFORMPLUGIN=
start "Brixigo Connector" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
