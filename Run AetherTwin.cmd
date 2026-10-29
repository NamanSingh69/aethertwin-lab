@echo off
setlocal
cd /d "%~dp0"
set "TWIN_PYTHON="
if exist ".venv\Scripts\python.exe" set "TWIN_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined TWIN_PYTHON if exist "..\..\work\locked_venv\Scripts\python.exe" set "TWIN_PYTHON=%~dp0..\..\work\locked_venv\Scripts\python.exe"
if not defined TWIN_PYTHON (
  echo Create the isolated Python 3.12 environment using the setup instructions in README.md.
  pause
  exit /b 1
)
"%TWIN_PYTHON%" -m command_center --mode sim --open-browser %*
if errorlevel 1 pause
endlocal
