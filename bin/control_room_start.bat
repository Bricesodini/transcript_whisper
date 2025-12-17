@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
set "CONTROL_ROOM_DIR=%REPO_ROOT%\control_room"
set "HOST=127.0.0.1"
set "EXTRA_ARGS=%*"

if /I "%~1"=="--lan" (
    set "HOST=0.0.0.0"
    shift
    set "EXTRA_ARGS=%*"
)

set "PYTHON_BIN="
if defined TS_VENV_DIR (
    if exist "%TS_VENV_DIR%\Scripts\python.exe" set "PYTHON_BIN=%TS_VENV_DIR%\Scripts\python.exe"
)
if not defined PYTHON_BIN (
    if exist "%REPO_ROOT%\.venv\Scripts\python.exe" (
        set "PYTHON_BIN=%REPO_ROOT%\.venv\Scripts\python.exe"
    )
)
if not defined PYTHON_BIN (
    echo [ERROR] Python introuvable dans le venv (.venv\Scripts\python.exe). >&2
    exit /b 1
)

pushd "%REPO_ROOT%"
echo [INFO] Lancement du Control Room sur %HOST%:8787
"%PYTHON_BIN%" -m uvicorn control_room.backend.app:app --host %HOST% --port 8787 %EXTRA_ARGS%
set "EXITCODE=%ERRORLEVEL%"
popd
exit /b %EXITCODE%
