@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

set ROOT=%~dp0..
set PYTHON=python
if defined QA_PYTHON (
  set PYTHON=%QA_PYTHON%
)

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set QA_TS=%%I
set LOGDIR=%ROOT%\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1
set QA_LOG=%LOGDIR%\qa_check_%QA_TS%.log
set FAILED=0

echo [QA] python: %PYTHON%
echo [QA] log file: %QA_LOG%

echo [QA] Transcribe Suite unit tests
pushd "%ROOT%"
"%PYTHON%" -m pytest transcribe-suite\tests\unit >> "%QA_LOG%" 2>&1
if errorlevel 1 set FAILED=1
popd

echo [QA] Control Room tests
pushd "%ROOT%"
"%PYTHON%" -m pytest tests\control_room >> "%QA_LOG%" 2>&1
if errorlevel 1 set FAILED=1
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
if errorlevel 1 set FAILED=1
popd

if not defined DATA_PIPELINE_ROOT (
  echo [QA] NAS audit skipped (DATA_PIPELINE_ROOT not set)
  echo [QA] NAS audit skipped (DATA_PIPELINE_ROOT not set) >> "%QA_LOG%"
  goto :after_nas
)

if "%DATA_PIPELINE_ROOT%"=="" (
  echo [QA] NAS audit skipped (DATA_PIPELINE_ROOT empty)
  echo [QA] NAS audit skipped (DATA_PIPELINE_ROOT empty) >> "%QA_LOG%"
  goto :after_nas
)

set "NAS_ROOT=%DATA_PIPELINE_ROOT%"
echo [QA] NAS root: %NAS_ROOT%
powershell -NoProfile -Command "if (Test-Path -LiteralPath '%NAS_ROOT%') { exit 0 } else { exit 1 }"
if errorlevel 1 (
  echo [QA] NAS audit skipped (DATA_PIPELINE_ROOT unreachable): %NAS_ROOT%
  echo [QA] NAS audit skipped (DATA_PIPELINE_ROOT unreachable): %NAS_ROOT% >> "%QA_LOG%"
  goto :after_nas
)

echo [QA] NAS audit (dry-run)
pushd "%ROOT%\transcribe-suite"
"%PYTHON%" -m tools.nas_audit --root "%NAS_ROOT%" --out-dir "%ROOT%\logs" >> "%QA_LOG%" 2>&1
if errorlevel 1 set FAILED=1
popd

goto :after_nas
:after_nas

echo [QA] Proceeding to smoke check
set QA_SMOKE_TIMEOUT_SEC=90
if defined QA_SMOKE_TIMEOUT (
  set QA_SMOKE_TIMEOUT_SEC=%QA_SMOKE_TIMEOUT%
)
for /f %%P in ('powershell -NoProfile -Command "Get-Random -Minimum 8200 -Maximum 8900"') do set QA_PORT=%%P
echo [QA] Control Room smoke (port %QA_PORT%, timeout %QA_SMOKE_TIMEOUT_SEC%s)
powershell -NoProfile -Command " $p=Start-Process -FilePath '%ROOT%\\bin\\control_room_smoke.bat' -ArgumentList @('--port','%QA_PORT%') -NoNewWindow -PassThru; if ($p.WaitForExit(%QA_SMOKE_TIMEOUT_SEC% * 1000)) { exit $p.ExitCode } else { $p.Kill(); exit 124 }" >> "%QA_LOG%" 2>&1
set /a SMOKE_RC=%ERRORLEVEL%
echo [QA] Smoke exit code: %SMOKE_RC% >> "%QA_LOG%"
echo [QA] Smoke exit code: %SMOKE_RC%
if %SMOKE_RC% equ 0 (
  echo [QA] Smoke OK >> "%QA_LOG%"
  echo [QA] Smoke OK
  goto :after_smoke
)
if %SMOKE_RC% equ 124 (
  echo [QA] Smoke timeout (port %QA_PORT%) >> "%QA_LOG%"
  echo [QA] Smoke timeout (port %QA_PORT%)
) else (
  echo [QA] Smoke failed (exit %SMOKE_RC%) >> "%QA_LOG%"
  echo [QA] Smoke failed (exit %SMOKE_RC%)
)
set FAILED=1

:after_smoke

if "%FAILED%"=="0" (
  echo [QA] OK
  exit /b 0
) else (
  echo [QA] FAILED (see %QA_LOG%)
  exit /b 1
)
