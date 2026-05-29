@echo off
setlocal
set "PYTHON_EXE=C:\Users\dsarr\AppData\Local\spyder-6\envs\palm-gpu311\python.exe"
set "PROJECT_ROOT=C:\PALM"
set "DIAG_SCRIPT=%PROJECT_ROOT%\launchers\palm_runtime_diagnostics.py"

echo [PALM] Running runtime diagnostics with palm-gpu311...
if not exist "%PYTHON_EXE%" (
    echo ERROR: Python interpreter not found: %PYTHON_EXE%
    exit /b 1
)
if not exist "%DIAG_SCRIPT%" (
    echo ERROR: Diagnostics helper not found: %DIAG_SCRIPT%
    exit /b 1
)

pushd "%PROJECT_ROOT%" || exit /b 1
"%PYTHON_EXE%" "%DIAG_SCRIPT%"
set "EXIT_CODE=%ERRORLEVEL%"
popd
pause
exit /b %EXIT_CODE%
