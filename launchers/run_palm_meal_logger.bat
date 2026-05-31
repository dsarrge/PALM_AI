@echo off
setlocal
set "PYTHON_EXE=C:\Users\dsarr\AppData\Local\spyder-6\envs\palm-gpu311\python.exe"
set "PROJECT_ROOT=C:\PALM"
set "APP_SCRIPT=%PROJECT_ROOT%\food_ai\ui\production_app.py"

echo [PALM] Launching production meal logging UI with palm-gpu311...
if not exist "%PYTHON_EXE%" (
    echo ERROR: Python interpreter not found: %PYTHON_EXE%
    exit /b 1
)
if not exist "%APP_SCRIPT%" (
    echo ERROR: Production Streamlit app not found: %APP_SCRIPT%
    exit /b 1
)

pushd "%PROJECT_ROOT%" || exit /b 1
"%PYTHON_EXE%" -m streamlit run "%APP_SCRIPT%"
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
