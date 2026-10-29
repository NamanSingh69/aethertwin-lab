@echo off
setlocal
cd /d "%~dp0"
set "LAB_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%LAB_PY%" set "LAB_PY=%~dp0..\..\work\locked_venv\Scripts\python.exe"
if not exist "%LAB_PY%" set "LAB_PY=%~dp0..\..\work\.venv\Scripts\python.exe"
if not exist "%LAB_PY%" (
  echo Create a Python 3.12 virtual environment as described in README.md.
  exit /b 1
)
"%LAB_PY%" main.py --mode sim %*
exit /b %ERRORLEVEL%
