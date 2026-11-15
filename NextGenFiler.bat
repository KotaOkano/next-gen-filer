@echo off
cd /d "%~dp0"

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