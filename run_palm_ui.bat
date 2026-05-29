@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "LAUNCHER=%SCRIPT_DIR%launchers\run_palm_ui.bat"

if not exist "%LAUNCHER%" (
    echo ERROR: Launcher not found: %LAUNCHER%
    exit /b 1
)

call "%LAUNCHER%"
exit /b %ERRORLEVEL%
