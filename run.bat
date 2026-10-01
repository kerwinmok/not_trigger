@echo off
rem Creates/updates a local virtual environment and runs the app from it.
rem Always uses the venv's own python.exe explicitly, so this can't pick
rem up the wrong interpreter even if this machine has several Pythons on
rem PATH (python.exe / MSYS2 / etc).

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3.12 -m venv .venv 2>nul
    if errorlevel 1 (
        python -m venv .venv
    )
)

echo Checking dependencies...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt

".venv\Scripts\python.exe" main.py

if errorlevel 1 (
    echo.
    echo Not Triggerbot exited with an error - see not_trigger.log for details.
    pause
)
