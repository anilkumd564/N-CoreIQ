@echo off
setlocal EnableDelayedExpansion

title N-CoreIQ — Starting
color 0B

echo.
echo  ================================================
echo    N-CoreIQ   SAP Repository Intelligence
echo  ================================================
echo.

REM -- Locate script directory (project root)
set "ROOT=%~dp0"
set "VENV_PYTHON=%ROOT%.venv\Scripts\python.exe"
set "FRONTEND_DIR=%ROOT%frontend"
set "DIST_INDEX=%ROOT%frontend\dist\index.html"
set "PORT=8000"

REM -- Verify virtual environment
if not exist "%VENV_PYTHON%" (
    echo  ERROR: Python virtual environment not found.
    echo         Expected: %VENV_PYTHON%
    echo.
    pause
    exit /b 1
)

REM -- Check if already running on port 8000
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo  N-CoreIQ is already running on port %PORT%.
    echo  Opening launcher...
    start "" "%ROOT%ncoreiq_launcher.html"
    echo.
    echo  To stop: run stop_ncoreiq.bat
    goto :END
)

REM -- Build frontend if dist is missing
if not exist "%DIST_INDEX%" (
    echo  [1/2] Building frontend for the first time...
    echo.

    where npm >nul 2>&1
    if errorlevel 1 (
        echo  ERROR: npm not found. Install Node.js from https://nodejs.org
        pause
        exit /b 1
    )

    cd /d "%FRONTEND_DIR%"

    if not exist "node_modules" (
        echo  Installing npm dependencies ^(one-time setup^)...
        call npm install --silent
        if errorlevel 1 (
            echo  ERROR: npm install failed.
            pause
            exit /b 1
        )
    )

    call npm run build
    if errorlevel 1 (
        echo  ERROR: Frontend build failed.
        pause
        exit /b 1
    )

    cd /d "%ROOT%"
    echo  Frontend built successfully.
    echo.
) else (
    echo  [1/2] Frontend ready.
)

REM -- Start N-CoreIQ server (minimized window, stays open)
echo  [2/2] Starting N-CoreIQ server...
start "N-CoreIQ Server" /MIN "%VENV_PYTHON%" -m uvicorn app.main:app --host 0.0.0.0 --port %PORT%

REM -- Wait for server to be ready (up to 15 seconds)
echo  Waiting for server...
set /a TRIES=0
:WAIT
timeout /t 1 /nobreak >nul
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto :READY
set /a TRIES+=1
if !TRIES! lss 15 goto :WAIT
echo  ERROR: Server did not respond within 15 seconds.
pause
exit /b 1

:READY
echo.
echo  ================================================
echo    N-CoreIQ is running!
echo.
echo    URL:   http://localhost:%PORT%/ui/
echo    API:   http://localhost:%PORT%/api/v1/
echo    Docs:  http://localhost:%PORT%/docs
echo.
echo    To stop: run stop_ncoreiq.bat
echo  ================================================
echo.

REM -- Open launcher page
start "" "%ROOT%ncoreiq_launcher.html"

:END
endlocal
