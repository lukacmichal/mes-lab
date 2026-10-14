@echo off
rem MES lab: MES + plant simulation on http://127.0.0.1:8070 (Ctrl+C stops both)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  python -m venv .venv
  .venv\Scripts\pip install -e ".[dev]"
)
.venv\Scripts\python -m mes_lab.dev --speed 60 --open %*
