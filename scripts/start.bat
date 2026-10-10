@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title StudyOS

rem ==============================================================
rem  StudyOS one-click launcher (Windows; mirrors start.sh)
rem  Backend FastAPI :8800, serves frontend web/dist
rem  - First run: create venv and install dependencies automatically
rem  - Already running: reuse, avoid port conflict
rem  - Listens on 0.0.0.0: LAN devices can visit http://LAN-IP:8800
rem  NOTE: keep this file pure ASCII. cmd parses batch files with the
rem  active code page, so Chinese text breaks it on non-UTF-8 systems.
rem  Also avoid parentheses in echo/rem text inside if-blocks, and
rem  avoid < > in text, they are redirection operators.
rem ==============================================================

rem ---------- LAN IP, hint for other devices; ignored on failure ----------
set "LAN_IP="
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "try{(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop | Where-Object {$_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*'} | Select-Object -First 1).IPAddress}catch{''}"`) do set "LAN_IP=%%I"

rem ---------- 0. Locate backend dir: this script lives in <root>\scripts ----------
rem    Also tolerates being copied into the project root or into backend\ itself.
cd /d "%~dp0..\backend" 2>nul
if errorlevel 1 cd /d "%~dp0backend" 2>nul
if errorlevel 1 cd /d "%~dp0" 2>nul
if not exist "app\main.py" (
    echo [ERROR] Backend dir not found. Run this script from the project tree: scripts\start.bat
    pause
    exit /b 1
)

rem ---------- 1. Locate Python, prefer py launcher, then python / python3 ----------
set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYCMD=python"
)
if not defined PYCMD (
    where python3 >nul 2>nul
    if not errorlevel 1 set "PYCMD=python3"
)

if not defined PYCMD (
    echo [ERROR] Python not found. Please install Python 3.10 or newer:
    echo        https://www.python.org/downloads/
    echo        During install, check "Add python.exe to PATH"
    pause
    exit /b 1
)

echo.
echo [*] Python version:
!PYCMD! --version

rem ---------- 2. Drop venvs that cannot run on Windows ----------
rem    A .venv copied from macOS/Linux has bin\ instead of Scripts\ and
rem    pyvenv.cfg pointing to /Library or /usr, which Windows cannot use.
rem    Also drop a venv whose base Python, the pyvenv.cfg home entry, no
rem    longer exists, e.g. after Python was moved or reinstalled.
if exist .venv (
    set "VENV_OK=1"
    if not exist ".venv\Scripts\python.exe" set "VENV_OK=0"
    if exist ".venv\pyvenv.cfg" (
        findstr /i /c:"/Library/" /c:"/usr/" /c:"/opt/" /c:"/Users/" /c:"/home/" ".venv\pyvenv.cfg" >nul 2>nul
        if not errorlevel 1 set "VENV_OK=0"
    )
    if "!VENV_OK!"=="1" (
        ".venv\Scripts\python.exe" -c "import sys,os;sys.exit(0 if os.path.exists(sys._base_executable) else 1)" >nul 2>nul
        if errorlevel 1 set "VENV_OK=0"
    )
    if "!VENV_OK!"=="0" (
        echo.
        echo [*] Broken venv detected, unusable on Windows or base Python missing. Recreating...
        rmdir /s /q .venv
        if exist .venv (
            echo [ERROR] Failed to delete .venv: close terminals still using it, then retry
            pause
            exit /b 1
        )
    )
)

rem ---------- 3. Create venv if missing ----------
if not exist .venv (
    echo.
    echo First run: creating virtual environment...
    !PYCMD! -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo [ERROR] Failed to create venv. Please make sure Python is fully installed
        pause
        exit /b 1
    )
)

rem ---------- 3b. Make sure pip exists inside the venv ----------
rem    Some Python builds ship without a working ensurepip, so "python -m venv"
rem    creates Scripts\python.exe but no pip inside. Bootstrap it with the
rem    system pip instead of failing later with "No module named pip".
".venv\Scripts\python.exe" -c "import pip" >nul 2>nul
if errorlevel 1 (
    echo.
    echo [*] pip missing in venv, bootstrapping it from the system Python...
    !PYCMD! -m pip --python ".venv\Scripts\python.exe" install --upgrade pip
    if errorlevel 1 (
        echo [ERROR] Could not bootstrap pip. Repair the Python install, delete backend\.venv, and retry
        pause
        exit /b 1
    )
)

rem ---------- 4. Install dependencies if uvicorn is missing ----------
if not exist ".venv\Scripts\uvicorn.exe" (
    echo.
    echo Installing dependencies, needs network, about 1-3 min...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        rem OCR component rapidocr-onnxruntime is optional. Not every
        rem Python version ships a wheel, or the package stopped at 1.2.3.
        rem Install core deps first, then try OCR separately. Warn only.
        echo.
        echo [*] Some deps failed. Retrying core deps first, OCR optional...
        findstr /v /i "rapidocr" requirements.txt > "%TEMP%\studyos-req-core.txt"
        ".venv\Scripts\python.exe" -m pip install -r "%TEMP%\studyos-req-core.txt"
        if errorlevel 1 (
            echo [ERROR] Core dependency install failed: check network and retry, or delete backend\.venv and rerun
            pause
            exit /b 1
        )
        echo.
        echo [*] Trying OCR component, optional, image-extract feature...
        ".venv\Scripts\python.exe" -m pip install rapidocr-onnxruntime
        if errorlevel 1 (
            echo.
            echo [WARN] OCR component failed: image-extract unavailable, rest is fine.
            echo        To enable it, upgrade Windows Python to 3.10+ and rerun this script.
        )
    )
)

rem ---------- 4b. Reinstall OCR component if missing ----------
rem    rapidocr-onnxruntime is optional; some Python versions may fail on
rem    first try. Check each launch; try to install if missing; warn only.
".venv\Scripts\python.exe" -c "import rapidocr_onnxruntime" >nul 2>nul
if errorlevel 1 (
    echo.
    echo [*] OCR component missing. Trying to install rapidocr-onnxruntime, optional...
    ".venv\Scripts\python.exe" -m pip install rapidocr-onnxruntime
    if errorlevel 1 (
        echo [WARN] OCR install failed: image-extract and schedule-photo OCR unavailable, rest is fine.
    ) else (
        echo [*] OCR component installed.
    )
)

rem ---------- 4c. Reinstall Excel parser if missing, openpyxl, pure Python ----------
".venv\Scripts\python.exe" -c "import openpyxl" >nul 2>nul
if errorlevel 1 (
    echo.
    echo [*] Excel parser missing. Trying to install openpyxl for schedule .xlsx import...
    ".venv\Scripts\python.exe" -m pip install openpyxl
    if errorlevel 1 (
        echo [WARN] openpyxl install failed: schedule XLSX import unavailable; .ics, image and text channels OK.
    ) else (
        echo [*] openpyxl installed.
    )
)

rem ---------- 5. Already running: reuse, idempotent, avoid port conflict ----------
set "HEALTHY=0"
where curl.exe >nul 2>nul
if not errorlevel 1 (
    curl.exe -sf --max-time 2 http://127.0.0.1:8800/api/health >nul 2>nul
    if not errorlevel 1 set "HEALTHY=1"
) else (
    powershell -NoProfile -Command "try{(Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:8800/api/health')|Out-Null;exit 0}catch{exit 1}" >nul 2>nul
    if not errorlevel 1 set "HEALTHY=1"
)

if "%HEALTHY%"=="1" (
    echo.
    echo StudyOS backend is already running, reusing it
    echo Opening browser at http://127.0.0.1:8800
    if defined LAN_IP echo LAN access for other devices: http://!LAN_IP!:8800
    start "" http://127.0.0.1:8800
    pause
    exit /b 0
)

rem ---------- 6. Start ----------
echo.
echo Starting StudyOS backend at http://127.0.0.1:8800, Ctrl+C to stop, listens on 0.0.0.0 for LAN
if defined LAN_IP echo LAN access for other devices: http://!LAN_IP!:8800
echo Hold Ctrl and click the address above, or paste it into a browser
echo.
".venv\Scripts\uvicorn.exe" app.api.main:app --host 0.0.0.0 --port 8800
if errorlevel 1 (
    echo.
    echo [ERROR] Backend exited abnormally, code !errorlevel!, check logs above
    pause
)

endlocal
