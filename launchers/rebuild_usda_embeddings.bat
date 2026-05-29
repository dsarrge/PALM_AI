@echo off
setlocal
set "PYTHON_EXE=C:\Users\dsarr\AppData\Local\spyder-6\envs\palm-gpu311\python.exe"
set "PROJECT_ROOT=C:\PALM"
set "USDA_SCRIPT=food_ai.tools.build_usda_embeddings"

echo [PALM] Rebuilding USDA embeddings with palm-gpu311...
if not exist "%PYTHON_EXE%" (
    echo ERROR: Python interpreter not found: %PYTHON_EXE%
    exit /b 1
)
if not exist "%PROJECT_ROOT%\food_ai\tools\build_usda_embeddings.py" (
    echo ERROR: USDA rebuild script not found: %PROJECT_ROOT%\food_ai\tools\build_usda_embeddings.py
    exit /b 1
)

pushd "%PROJECT_ROOT%" || exit /b 1
"%PYTHON_EXE%" -m %USDA_SCRIPT% --output "%PROJECT_ROOT%\food_ai\data\usda_embeddings.pkl"
set "EXIT_CODE=%ERRORLEVEL%"
popd
pause
exit /b %EXIT_CODE%
