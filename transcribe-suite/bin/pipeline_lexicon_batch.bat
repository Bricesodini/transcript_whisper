@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PIPELINE_ROOT=\\bricesodini\Savoirs\03_data_pipeline"
set "STAGING_ASR=%PIPELINE_ROOT%\02_output_source\asr"
set "BIN_DIR=%~dp0"
set "RUN_BAT=%BIN_DIR%run.bat"
for %%I in ("%BIN_DIR%..") do set "REPO_ROOT=%%~fI"
if not exist "%RUN_BAT%" (
  echo [ERROR] run.bat introuvable.
  goto FAIL
)
set "FORCE_MODE=0"
set "DO_APPLY=0"
set "MAX_DOCS=0"
set "DOC_FILTER="
set "LEXICON_EXTRA="
set "AFTER_DASH=0"
:ParseArgs
if "%~1"=="" goto ArgsDone
if "%AFTER_DASH%"=="1" (
  set "LEXICON_EXTRA=%LEXICON_EXTRA% %~1"
  shift
  goto ParseArgs
)
if "%~1"=="--" (
  set "AFTER_DASH=1"
  shift
  goto ParseArgs
)
if /I "%~1"=="--force" (
  set "FORCE_MODE=1"
  shift
  goto ParseArgs
)
if /I "%~1"=="--scan-only" (
  set "DO_APPLY=0"
  shift
  goto ParseArgs
)
if /I "%~1"=="--apply" (
  set "DO_APPLY=1"
  shift
  goto ParseArgs
)
if /I "%~1"=="--max-docs" (
  shift
  if "%~1"=="" goto ArgsDone
  set "MAX_DOCS=%~1"
  shift
  goto ParseArgs
)
if /I "%~1"=="--doc" (
  shift
  if "%~1"=="" goto ArgsDone
  set "DOC_FILTER=%~1"
  shift
  goto ParseArgs
)
set "LEXICON_EXTRA=%LEXICON_EXTRA% %~1"
shift
goto ParseArgs
:ArgsDone
if not exist "%STAGING_ASR%" (
  echo [ERROR] Staging ASR introuvable: %STAGING_ASR%
  goto FAIL
)
set /a DOC_VISITED=0
set /a DOC_SCAN=0
set /a DOC_APPLY=0
set /a DOC_SKIP=0
set /a DOC_FAIL=0
echo [INFO] Pipeline lexicon sur %STAGING_ASR%
for /d %%D in ("%STAGING_ASR%\*") do (
  call :ProcessDoc "%%D"
  if %MAX_DOCS% GTR 0 (
    if !DOC_VISITED! GEQ %MAX_DOCS% goto DoneLoop
  )
)
:DoneLoop
echo.
echo === Pipeline lexicon terminée ===
echo Traités : %DOC_VISITED%
echo Scan    : %DOC_SCAN%
echo Apply   : %DOC_APPLY%
echo Skip    : %DOC_SKIP%
echo Fail    : %DOC_FAIL%
if %DOC_FAIL% GTR 0 (
  exit /b 1
) else (
  exit /b 0
)

:ProcessDoc
set "DOC_STAGE=%~1"
if not exist "%DOC_STAGE%" goto :EOF
for %%C in ("%DOC_STAGE%") do set "DOC_STAGE_NAME=%%~nxC"
if defined DOC_FILTER (
  echo.%DOC_STAGE_NAME%| findstr /I /C:"%DOC_FILTER%" >nul
  if errorlevel 1 (
    goto :EOF
  )
)
set "WORK_DIR="
call :ResolveWorkDir "%DOC_STAGE%"
if not defined WORK_DIR (
  echo [WARN] Work introuvable pour %DOC_STAGE%
  goto :EOF
)
set /a DOC_VISITED+=1
echo.
echo === LEXICON ===
echo Doc  : %DOC_STAGE_NAME%
echo Work : %WORK_DIR%
set "VALIDATED=%WORK_DIR%\rag.glossary.yaml"
set "STAMP_PATH=%WORK_DIR%\.lexicon_ok.json"
set "SOURCE_FILE_PATH="
set "SOURCE_FILE_NAME="
call :SelectSourceFile "%WORK_DIR%"
set "FILE_SHA="
set "STAMP_SOURCE_FILE="
set "STAMP_SOURCE_SHA="
if exist "%STAMP_PATH%" call :ReadStamp "%STAMP_PATH%"
if exist "%VALIDATED%" (
  if "%FORCE_MODE%"=="0" (
    if defined STAMP_SOURCE_FILE (
      if /I "%STAMP_SOURCE_FILE%"=="%SOURCE_FILE_NAME%" (
        if defined SOURCE_FILE_PATH (
          call :ComputeSha256 "%SOURCE_FILE_PATH%"
          if defined FILE_SHA (
            if /I "%FILE_SHA%"=="%STAMP_SOURCE_SHA%" (
              echo State: SKIP_VALIDATED (hash inchangé)
              set /a DOC_SKIP+=1
              goto :EOF
            )
          )
        )
      )
    )
  )
)
call "%RUN_BAT%" rag lexicon scan --input "%WORK_DIR%" %LEXICON_EXTRA%
if errorlevel 1 goto ProcessDocFail
set /a DOC_SCAN+=1
set "LAST_STATE=SCANNED"
if "%DO_APPLY%"=="1" (
  call "%RUN_BAT%" rag lexicon apply --input "%WORK_DIR%" %LEXICON_EXTRA%
  if errorlevel 1 goto ProcessDocFail
  set /a DOC_APPLY+=1
  set "LAST_STATE=APPLIED"
)
echo State: %LAST_STATE%
goto :EOF

:ProcessDocFail
echo State: FAIL
set /a DOC_FAIL+=1
goto :EOF

:ResolveWorkDir
set "DOC_ROOT=%~1"
set "WORK_PARENT=%DOC_ROOT%\work"
if not exist "%WORK_PARENT%" exit /b 1
set "DOC_SPECIFIC=%WORK_PARENT%\%DOC_STAGE_NAME%"
if exist "%DOC_SPECIFIC%" (
  set "WORK_DIR=%DOC_SPECIFIC%"
  exit /b 0
)
set /a WORK_COUNT=0
set "WORK_TMP="
for /d %%W in ("%WORK_PARENT%\*") do (
  set "WORK_TMP=%%~fW"
  set /a WORK_COUNT+=1
)
if %WORK_COUNT% EQU 1 (
  set "WORK_DIR=%WORK_TMP%"
  exit /b 0
)
exit /b 1

:SelectSourceFile
set "SOURCE_FILE_PATH="
set "SOURCE_FILE_NAME="
for %%F in ("05_polished.json" "04_cleaned.json" "02_merged_raw.json") do (
  if exist "%~1\%%~F" (
    set "SOURCE_FILE_PATH=%~1\%%~F"
    set "SOURCE_FILE_NAME=%%~F"
    goto :EOF
  )
)
goto :EOF

:ComputeSha256
set "FILE_SHA="
for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command ^
  "param([string]$p); if (Test-Path -LiteralPath $p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant() }" ^
  "%~1"`) do (
  set "FILE_SHA=%%H"
)
if not defined FILE_SHA exit /b 1
exit /b 0

:ReadStamp
for /f "usebackq tokens=1,* delims==" %%A in (`powershell -NoProfile -Command ^
  "param([string]$p); if (Test-Path -LiteralPath $p) { $json=Get-Content -Raw -LiteralPath $p | ConvertFrom-Json; if($json){ $src=$json.source_file; $sha=$json.source_sha256; if($null -eq $src){$src=''}; if($null -eq $sha){$sha=''}; Write-Output ('STAMP_SOURCE_FILE='+$src); Write-Output ('STAMP_SOURCE_SHA='+$sha); } }" ^
  "%~1"`) do (
    if /I "%%A"=="STAMP_SOURCE_FILE" set "STAMP_SOURCE_FILE=%%B"
    if /I "%%A"=="STAMP_SOURCE_SHA" set "STAMP_SOURCE_SHA=%%B"
)
exit /b 0

:FAIL
echo.
echo ECHEC pipeline_lexicon_batch
exit /b 1
