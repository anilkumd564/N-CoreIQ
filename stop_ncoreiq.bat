@echo off
setlocal EnableDelayedExpansion

title N-CoreIQ — Stopping
color 0C

echo.
echo  ================================================
echo    N-CoreIQ   Stopping...
echo  ================================================
echo.

set "PORT=8000"
set "FOUND=0"

REM -- Find and kill all processes listening on port 8000
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    if "%%P" NEQ "0" (
        echo  Stopping process PID %%P on port %PORT%...
        taskkill /PID %%P /F >nul 2>&1
        set "FOUND=1"
    )
)

REM -- Also kill any lingering uvicorn / python processes with "uvicorn" in command
REM    (catches child worker processes spawned with --reload)
for /f "tokens=2" %%P in ('tasklist /FI "IMAGENAME eq python.exe" /FO CSV /NH 2^>nul') do (
    set "PID=%%~P"
    wmic process where "ProcessId=!PID!" get CommandLine /VALUE 2>nul | findstr /I "uvicorn" >nul 2>&1
    if not errorlevel 1 (
        echo  Stopping Python/uvicorn worker PID !PID!...
        taskkill /PID !PID! /F >nul 2>&1
        set "FOUND=1"
    )
)

echo.
if "!FOUND!"=="1" (
    echo  N-CoreIQ server stopped successfully.
) else (
    echo  No N-CoreIQ server processes were found on port %PORT%.
    echo  The server may not have been running.
)

echo.
timeout /t 2 /nobreak >nul

endlocal
