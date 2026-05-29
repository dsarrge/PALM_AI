@echo off
setlocal
set "PYTHON_EXE=C:\Users\dsarr\AppData\Local\spyder-6\envs\palm-gpu311\python.exe"
set "PROJECT_ROOT=C:\PALM"

echo [PALM] Opening cmd shell with palm-gpu311 python pinned...
if not exist "%PYTHON_EXE%" (
    echo ERROR: Python interpreter not found: %PYTHON_EXE%
    exit /b 1
)
if not exist "%PROJECT_ROOT%" (
    echo ERROR: Project root not found: %PROJECT_ROOT%
    exit /b 1
)

pushd "%PROJECT_ROOT%" || exit /b 1
cmd /K "echo PALM shell ready. && echo Python: %PYTHON_EXE% && set PALM_PYTHON=%PYTHON_EXE% && doskey py="%PYTHON_EXE%" $*"
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
