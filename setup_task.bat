@echo off
setlocal EnableDelayedExpansion

:: ─────────────────────────────────────────────────────────────
::  HealthJobScraper — Windows Task Scheduler registration
::  Run as Administrator
:: ─────────────────────────────────────────────────────────────

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: This script must be run as Administrator.
    echo Right-click setup_task.bat and choose "Run as administrator".
    pause
    exit /b 1
)

:: Auto-detect first Python on PATH
for /f "usebackq tokens=*" %%i in (`where python 2^>nul`) do (
    if not defined PYTHON_EXE set "PYTHON_EXE=%%i"
)

if not defined PYTHON_EXE (
    echo ERROR: Python not found in PATH.
    echo Install Python from python.org and ensure "Add to PATH" is checked.
    pause
    exit /b 1
)

echo Found Python: %PYTHON_EXE%

set "SCRIPT_PATH=%~dp0scraper.py"
set "TASK_NAME=HealthJobScraper"
set "TASK_TIME=23:59"

echo.
echo Removing any existing "%TASK_NAME%" task...
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

echo Creating task: run daily at %TASK_TIME%...
schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "\"%PYTHON_EXE%\" \"%SCRIPT_PATH%\"" ^
    /sc DAILY ^
    /st %TASK_TIME% ^
    /ru SYSTEM ^
    /f

if %errorLevel% equ 0 (
    echo.
    echo SUCCESS  Task "%TASK_NAME%" registered.
    echo          Runs daily at %TASK_TIME% as SYSTEM (no login required).
    echo.
    echo To verify : open Task Scheduler ^> Task Scheduler Library
    echo To run now: schtasks /run /tn "%TASK_NAME%"
    echo To remove : schtasks /delete /tn "%TASK_NAME%" /f
) else (
    echo.
    echo ERROR: schtasks failed. Check that you have permission to create tasks.
)

pause
endlocal
