@echo off
setlocal enabledelayedexpansion
title Authority Agent
cd /d "%~dp0"

:: ---------------------------------------------------------------------------
:: Find Python. Prefer a local venv if one exists, else whatever is on PATH.
:: ---------------------------------------------------------------------------
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    where py >nul 2>&1 && set "PY=py"
)
if not defined PY (
    echo.
    echo   Python was not found.
    echo   Install it from https://python.org and tick "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

:: ---------------------------------------------------------------------------
:: Pick a free port starting at 8788, so a leftover process does not block us.
:: ---------------------------------------------------------------------------
set PORT=8788
for /l %%p in (8788,1,8798) do (
    netstat -ano | findstr /r /c:"LISTENING.*:%%p " >nul 2>&1
    if errorlevel 1 (
        set PORT=%%p
        goto :gotport
    )
)
:gotport

:menu
cls
echo.
echo   ===========================================
echo     AUTHORITY AGENT
echo   ===========================================
echo.
echo     1.  Open dashboard          (recommended)
echo     2.  Run daily cycle         (find + score + draft)
echo     3.  Show the queue
echo     4.  Activity report
echo     5.  Site status
echo.
echo     7.  Connect Reddit account  (log in, once per site)
echo     8.  Check which account is logged in
echo     9.  Post one reply          (asks before sending)
echo.
echo     6.  Quit
echo.
set "choice="
set /p choice="   Choose [1]: "
if not defined choice set choice=1

if "%choice%"=="1" goto dashboard
if "%choice%"=="2" goto daily
if "%choice%"=="3" goto queue
if "%choice%"=="4" goto report
if "%choice%"=="5" goto status
if "%choice%"=="7" goto connect
if "%choice%"=="8" goto whoami
if "%choice%"=="9" goto postone
if "%choice%"=="6" exit /b 0
goto menu

:: ---------------------------------------------------------------------------
:dashboard
cls
echo.
echo   Starting dashboard on port %PORT% ...
echo   Opening http://localhost:%PORT% in your browser.
echo.
echo   Leave this window OPEN while you use it.
echo   Press Ctrl+C here to stop.
echo.
start "" "http://localhost:%PORT%"
"%PY%" serve.py %PORT%
echo.
echo   Dashboard stopped.
pause
goto menu

:: ---------------------------------------------------------------------------
:daily
cls
echo.
echo   Running the daily cycle. This takes about 10 minutes.
echo   It finds threads, scores them and writes drafts.
echo   Nothing is posted.
echo.
"%PY%" daily.py
echo.
pause
goto menu

:: ---------------------------------------------------------------------------
:queue
cls
"%PY%" run.py queue
echo.
pause
goto menu

:: ---------------------------------------------------------------------------
:report
cls
"%PY%" run.py report
echo.
pause
goto menu

:: ---------------------------------------------------------------------------
:status
cls
"%PY%" run.py status
echo.
pause
goto menu

:: ---------------------------------------------------------------------------
:connect
cls
echo.
echo   Your sites:
echo.
"%PY%" run.py sites
echo.
echo   ------------------------------------------------------------
echo   A browser window will open on the Reddit login page.
echo   Log in there by hand, then come back here and press Enter.
echo.
echo   Each site keeps its own separate browser profile, so logging
echo   in here does not touch your normal Chrome or any other site.
echo   ------------------------------------------------------------
echo.
echo   Type the site NUMBER shown above (the "#1" part).
echo   Just press Enter if you only have one site.
echo.
set "sid="
set /p sid="   Site number: "
if not defined sid (
    "%PY%" run.py connect
) else (
    "%PY%" run.py connect --site %sid%
)
echo.
pause
goto menu

:: ---------------------------------------------------------------------------
:whoami
cls
"%PY%" run.py whoami
echo.
pause
goto menu

:: ---------------------------------------------------------------------------
:postone
cls
echo.
echo   This shows you one draft and asks before sending anything.
echo.
echo   Type the site NUMBER, or just press Enter for your only site.
echo.
set "sid="
set /p sid="   Site number: "
if not defined sid (
    "%PY%" run.py post
) else (
    "%PY%" run.py post --site %sid%
)
echo.
pause
goto menu
