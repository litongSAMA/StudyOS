@echo off
rem ==============================================================
rem  StudyOS backend regression tests (Windows; mirrors test.sh)
rem  Usage: scripts\test.bat [pytest args...]
rem    scripts\test.bat                        :: full suite
rem    scripts\test.bat tests\test_notes.py -v :: one module, verbose
rem  Tests use a throwaway temp DB and never touch data\studyos.db.
rem  Set STUDYOS_TEST_TMP to pin the temp DB root, e.g. on CI.
rem  NOTE: keep this file pure ASCII (cmd parses it with the active
rem  code page, so Chinese text would break on non-UTF-8 systems).
rem ==============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0.." || exit /b 1

if not exist ".venv\Scripts\python.exe" (
    echo [*] Creating virtual environment...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo [ERROR] Could not create .venv. Install Python 3.10+ and retry.
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || exit /b 1
)

".venv\Scripts\python.exe" -m pip install -q -r requirements-dev.txt
".venv\Scripts\python.exe" -m pytest %*
exit /b %errorlevel%
