@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "ROOT_DIR=%SCRIPT_DIR%.."
set "REPO_ROOT=%ROOT_DIR%\.."
if defined PYTHON (
    set "PYTHON_BIN=%PYTHON%"
) else if exist "%REPO_ROOT%\.venv\Scripts\python.exe" (
    set "PYTHON_BIN=%REPO_ROOT%\.venv\Scripts\python.exe"
) else if exist "%REPO_ROOT%\.venv\bin\python" (
    set "PYTHON_BIN=%REPO_ROOT%\.venv\bin\python"
) else (
    set "PYTHON_BIN=python"
)
"%PYTHON_BIN%" "%ROOT_DIR%\bin\env_check.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
