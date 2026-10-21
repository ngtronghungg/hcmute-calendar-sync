@echo off
chcp 65001 > nul
cd /d "%~dp0"

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    ) else (
        where python >nul 2>&1
        if not errorlevel 1 (
            set "PYTHON_EXE=python"
        )
    )
)

if not exist "%PYTHON_EXE%" (
    echo.
    echo ======================================================================
    echo [LOI] Khong tim thay Python!
    echo ======================================================================
    pause
    exit /b 1
)

if not "%~1"=="" (
    "%PYTHON_EXE%" "%~dp0main.py" %*
    goto :eof
)

echo.
echo ======================================================================
echo UTECAL - HCMUTE CALENDAR ^& SYNC TOOL
echo Dang khoi dong Giao dien UTECal Dashboard...
echo Trinh duyet web se tu dong mo tai: http://127.0.0.1:5000
echo ======================================================================
echo.

"%PYTHON_EXE%" "%~dp0src\web_server.py"

pause