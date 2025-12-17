@echo off
setlocal EnableExtensions

set ROOT=%~dp0..
set PKG=%ROOT%\transcribe-suite
set PYTHON=%ROOT%\.venv\Scripts\python.exe
if not exist "%PYTHON%" (
  set PYTHON=python
)

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TS=%%I
set LOGDIR=%ROOT%\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1
set LOGFILE=%LOGDIR%\cleanup_%TS%.log

pushd "%ROOT%\transcribe-suite"
"%PYTHON%" -m tools.cleanup_audit ^
  --repo-root "%PKG%" ^
  --out-dir "%LOGDIR%" ^
  --log-file "%LOGFILE%" ^
  %*
set EXITCODE=%ERRORLEVEL%
popd

if %EXITCODE% neq 0 (
  echo [cleanup] Echec (voir "%LOGFILE%")
  exit /b %EXITCODE%
)
echo [cleanup] Audit termine (rapport/logs dans "%LOGDIR%", log: "%LOGFILE%")
exit /b 0
