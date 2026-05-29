@echo off
setlocal
set "PYTHON_EXE=C:\Users\dsarr\AppData\Local\spyder-6\envs\palm-gpu311\python.exe"
set "PROJECT_ROOT=C:\PALM"
set "SMOKE_SCRIPT=%PROJECT_ROOT%\launchers\palm_food_smoke_test.py"

echo [PALM] Running food vision smoke test with palm-gpu311...
if not exist "%PYTHON_EXE%" (
    echo ERROR: Python interpreter not found: %PYTHON_EXE%
    exit /b 1
)
if not exist "%SMOKE_SCRIPT%" (
    echo ERROR: Smoke test helper not found: %SMOKE_SCRIPT%
    exit /b 1
)

pushd "%PROJECT_ROOT%" || exit /b 1
"%PYTHON_EXE%" "%SMOKE_SCRIPT%"
set "EXIT_CODE=%ERRORLEVEL%"
popd
pause
exit /b %EXIT_CODE%
