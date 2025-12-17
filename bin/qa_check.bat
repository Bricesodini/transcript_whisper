@echo off
setlocal EnableExtensions

set ROOT=%~dp0..
set PYTHON=python
if defined QA_PYTHON (
  set PYTHON=%QA_PYTHON%
)

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set QA_TS=%%I
set LOGDIR=%ROOT%\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1
set QA_LOG=%LOGDIR%\qa_check_%QA_TS%.log

echo [QA] python: %PYTHON%
echo [QA] log file: %QA_LOG%

echo [QA] Transcribe Suite unit tests
pushd "%ROOT%"
"%PYTHON%" -m pytest transcribe-suite\tests\unit >> "%QA_LOG%" 2>&1
if errorlevel 1 goto fail
popd

echo [QA] Control Room tests
pushd "%ROOT%"
"%PYTHON%" -m pytest tests\control_room >> "%QA_LOG%" 2>&1
if errorlevel 1 goto fail
popd

for %%D in (inputs exports) do (
  if exist "%ROOT%\transcribe-suite\%%D" (
    echo [QA] removing leftover %%D directory
    rmdir /s /q "%ROOT%\transcribe-suite\%%D"
  )
)

echo [QA] Repository audit (dry-run)
pushd "%ROOT%\transcribe-suite"
"%PYTHON%" -m tools.cleanup_audit --repo-root "%ROOT%\transcribe-suite" --out-dir "%ROOT%\logs" --fail-on-legacy >> "%QA_LOG%" 2>&1
echo [QA] cleanup_audit exit code: %ERRORLEVEL%
if errorlevel 1 goto fail
popd

set "NAS_ROOT=%DATA_PIPELINE_ROOT%"
echo [QA] NAS root: %NAS_ROOT%
if "%NAS_ROOT%"=="" (
  echo [QA] NAS audit skipped (DATA_PIPELINE_ROOT not set^)
  echo [QA] NAS audit skipped (DATA_PIPELINE_ROOT not set^) >> "%QA_LOG%"
) else (
  echo [QA] NAS audit (dry-run)
  pushd "%ROOT%\transcribe-suite"
  "%PYTHON%" -m tools.nas_audit --root "%NAS_ROOT%" --out-dir "%ROOT%\logs" >> "%QA_LOG%" 2>&1
  if errorlevel 1 goto fail
  popd
)

echo [QA] Proceeding to smoke check

for /f %%P in ('powershell -NoProfile -Command "Get-Random -Minimum 8200 -Maximum 8900"') do set QA_PORT=%%P
echo [QA] Control Room smoke (port %QA_PORT%)
"%ROOT%\bin\control_room_smoke.bat" --port %QA_PORT% >> "%QA_LOG%" 2>&1
if errorlevel 1 goto fail

echo [QA] OK
exit /b 0

:fail
echo [QA] FAILED (see %QA_LOG%)
exit /b 1
