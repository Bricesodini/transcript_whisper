@echo off
setlocal EnableExtensions

set ROOT=%~dp0..
set PYTHON=%ROOT%\.venv\Scripts\python.exe
if not exist "%PYTHON%" (
  set PYTHON=python
)

echo [cleanup-stage] Preparing staging copy...

pushd "%ROOT%\transcribe-suite"
"%PYTHON%" -m tools.stage_cleanup --repo-root "%ROOT%" %*
if errorlevel 1 goto fail
popd
echo [cleanup-stage] Done (see staging folder above)
exit /b 0

:fail
echo [cleanup-stage] FAILED (see log above)
exit /b 1
