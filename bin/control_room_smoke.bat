@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."

if not exist "%REPO_ROOT%\.venv\Scripts\python.exe" (
    echo [SMOKE] ERROR: .venv not found at %REPO_ROOT%\.venv
    exit /b 1
)

set "PYTHON_EXE=%REPO_ROOT%\.venv\Scripts\python.exe"
pushd "%REPO_ROOT%" >nul
"%PYTHON_EXE%" -m control_room.backend.smoke %*
set "RC=%ERRORLEVEL%"
popd >nul

if %RC% neq 0 (
    echo [SMOKE] FAILED (%RC%)
    exit /b %RC%
)

echo [SMOKE] OK
exit /b 0
