@echo off
cd /d "%~dp0"

rem ============================================================
rem  NextGenFiler launcher
rem  - Distribution (python\python.exe exists): use embedded Python
rem  - Source / git clone (no embedded Python): use system Python + .venv
rem ============================================================

if exist "python\python.exe" goto :dist
goto :dev

rem ---------------- Distribution ----------------
:dist
if not exist "main.py" (
    echo [ERROR] main.py was not found. Please extract the whole ZIP and try again.
    pause
    exit /b
)

if not exist "python\.setup_done" (
    echo ========================================================
    echo  Setting up NextGenFiler for the first time...
    echo  Please wait a few minutes. Do not close this window.
    echo ========================================================
    echo.

    "python\python.exe" -m pip --version >nul 2>&1
    if errorlevel 1 (
        echo [1/2] Installing pip...
        "python\python.exe" "python\get-pip.py" --no-warn-script-location
        if errorlevel 1 (
            echo.
            echo [ERROR] pip installation failed.
            pause
            exit /b
        )
    )

    echo [2/2] Installing required libraries...
    "python\python.exe" -m pip install -r requirements.txt --no-warn-script-location

    if errorlevel 1 (
        echo.
        echo [ERROR] Setup failed. Please check your internet connection.
        pause
        exit /b
    )

    echo done > "python\.setup_done"
    echo.
    echo Setup completed! Starting NextGenFiler...
)

start "" "python\pythonw.exe" "main.py"
exit /b

rem ---------------- Source / development ----------------
:dev
echo ========================================================
echo  Embedded Python was not found. Running in SOURCE mode.
echo  System Python and a .venv will be used instead.
echo ========================================================
echo.

set "PYEXE="
where py >nul 2>&1
if not errorlevel 1 set "PYEXE=py -3"
if defined PYEXE goto :dev_venv
where python >nul 2>&1
if not errorlevel 1 set "PYEXE=python"
if defined PYEXE goto :dev_venv

echo [ERROR] Python was not found on this PC.
echo Please install Python 3.12 or later from https://www.python.org/
echo (check "Add python.exe to PATH" during installation), then try again.
pause
exit /b

:dev_venv
if exist ".venv\Scripts\python.exe" goto :dev_run

echo [1/2] Creating virtual environment (.venv)...
%PYEXE% -m venv .venv
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to create the virtual environment.
    pause
    exit /b
)

echo [2/2] Installing required libraries...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Setup failed. Please check your internet connection.
    pause
    exit /b
)
echo Setup completed! Starting NextGenFiler...

:dev_run
start "" ".venv\Scripts\pythonw.exe" "main.py"
exit /b
