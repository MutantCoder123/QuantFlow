@echo off
REM Run the whole QuantFlow system: checks, history catch-up, all services.
REM   run.bat --check        checks only
REM   run.bat --no-backfill  skip the history catch-up
REM   run.bat --verbose      show every service's output
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo No virtualenv at .venv. Create it first:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
".venv\Scripts\python.exe" run.py %*
if errorlevel 1 pause
