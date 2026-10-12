@echo off
setlocal
cd /d "%~dp0"
py src/analyzer.py
if errorlevel 1 pause
