@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title StudyOS - stop

rem ==============================================================
rem  StudyOS stop script (Windows; mirrors stop.sh)
rem  Stops the backend that scripts\start.bat (or uvicorn) started.
rem
rem  Safety, same policy as stop.sh:
rem    - only kill a process on port 8800 whose command line is really
rem      our uvicorn, so an unrelated program that grabbed the port and
rem      a recycled PID can never be killed by mistake
rem    - try a graceful stop first, force only if it does not exit
rem
rem  NOTE: keep this file pure ASCII. cmd parses batch files with the
rem  active code page, so non-ASCII text breaks on non-UTF-8 systems.
rem ==============================================================

echo Stopping StudyOS backend on port 8800...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='SilentlyContinue';" ^
  "$port=8800;" ^
  "$conns=Get-NetTCPConnection -LocalPort $port -State Listen;" ^
  "if(-not $conns){ Write-Host 'StudyOS backend is not running (nothing listening on port 8800)'; exit 0 }" ^
  "$pid_=($conns | Select-Object -First 1).OwningProcess;" ^
  "$p=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $pid_);" ^
  "$cmd=if($p){[string]$p.CommandLine}else{''};" ^
  "$venv=Join-Path (Split-Path -Parent $PSScriptRoot) 'backend\.venv\Scripts\uvicorn.exe';" ^
  "$ours = ($cmd -like '*uvicorn*' -and $cmd -like '*app.main*') -or ($cmd -like '*uvicorn*' -and (Test-Path $venv));" ^
  "if(-not $ours){ Write-Host ('[ERROR] PID ' + $pid_ + ' is not this project''s uvicorn; not stopping it.') -ForegroundColor Red; Write-Host ('        command line: ' + $cmd); exit 1 }" ^
  "Write-Host ('Stopping PID ' + $pid_ + ' ...');" ^
  "Stop-Process -Id $pid_ -ErrorAction SilentlyContinue;" ^
  "$gone=$false;" ^
  "for($i=0;$i -lt 10;$i++){ Start-Sleep -Seconds 1; if(-not (Get-NetTCPConnection -LocalPort $port -State Listen)){ $gone=$true; break } }" ^
  "if(-not $gone){ Write-Host 'Still listening after 10s, forcing...'; Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 }" ^
  "if(Get-NetTCPConnection -LocalPort $port -State Listen){ Write-Host ('[WARN] port ' + $port + ' is still in use by another process') -ForegroundColor Yellow; exit 1 }" ^
  "Write-Host 'StudyOS backend stopped (port 8800 released)'"

set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
    echo.
    echo [ERROR] Stop failed with code %CODE%. See the message above.
    pause
)
endlocal & exit /b %CODE%
