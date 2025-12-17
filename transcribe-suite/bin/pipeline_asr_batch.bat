@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
echo [DEBUG] Batch OK - setlocal fonctionne.
set "PIPELINE_ROOT=\\bricesodini\Savoirs\03_data_pipeline"
set "INPUT_ROOT=%PIPELINE_ROOT%\01_input"
set "INPUT_AUDIO=%INPUT_ROOT%\audio"
set "INPUT_VIDEO=%INPUT_ROOT%\video"
set "OUTPUT_SOURCE=%PIPELINE_ROOT%\02_output_source\asr"
set "OUTPUT_PDF=%PIPELINE_ROOT%\02_output_source\pdf"
set "AUDIO_PROCESSED=%INPUT_AUDIO%\_processed"
set "AUDIO_FAILED=%INPUT_AUDIO%\_failed"
set "VIDEO_PROCESSED=%INPUT_VIDEO%\_processed"
set "VIDEO_FAILED=%INPUT_VIDEO%\_failed"
set "PDF_INPUT=%INPUT_ROOT%\pdf"
set "BIN_DIR=%~dp0"
set "RUN_BAT=%BIN_DIR%run.bat"
for %%I in ("%BIN_DIR%..") do set "REPO_ROOT=%%~fI"
set "EXTRA_ARGS=%*"
call :EnsureDir "%PIPELINE_ROOT%"
if errorlevel 1 goto FAIL
call :EnsureDir "%INPUT_ROOT%"
if errorlevel 1 goto FAIL
call :EnsureDir "%INPUT_AUDIO%"
call :EnsureDir "%INPUT_VIDEO%"
call :EnsureDir "%PDF_INPUT%"
call :EnsureDir "%OUTPUT_SOURCE%"
call :EnsureDir "%OUTPUT_PDF%"
call :EnsureDir "%AUDIO_PROCESSED%"
call :EnsureDir "%AUDIO_FAILED%"
call :EnsureDir "%VIDEO_PROCESSED%"
call :EnsureDir "%VIDEO_FAILED%"
if not exist "%RUN_BAT%" (
  echo [ERROR] run.bat introuvable.
  goto FAIL
)
set /a FILES_OK=0
set /a FILES_FAIL=0
call :ProcessDir "%INPUT_VIDEO%" "video"
call :ProcessDir "%INPUT_AUDIO%" "audio"
echo.
echo === Pipeline ASR terminée ===
echo OK   : %FILES_OK%
echo FAIL : %FILES_FAIL%
if %FILES_FAIL% gtr 0 (
  exit /b 1
) else (
  exit /b 0
)
:ProcessDir
set "SCAN_DIR=%~1"
set "SCAN_KIND=%~2"
if not exist "%SCAN_DIR%" (
  echo [WARN] Dossier introuvable: %SCAN_DIR%
  goto :EOF
)
for %%E in (mp4 mov mkv mp3 wav m4a) do (
  for /f "delims=" %%F in ('dir /b /a:-d "%SCAN_DIR%\*.%%E" 2^>nul') do (
    call :ProcessOne "%SCAN_DIR%\%%F" "%SCAN_KIND%"
  )
)
goto :EOF
:ProcessOne
set "SRC=%~1"
set "SRC_KIND=%~2"
set "RUN_FAILED=0"
echo.
echo === Traitement pipeline ASR ===
echo Fichier : %SRC%
for %%Z in ("%SRC%") do (
set "NAME=%%~nxZ"
set "BASE=%%~nZ"
set "SRC_DIR=%%~dpZ"
)
set "TS_PS_NAME=%NAME%"
set "SAFE="
for /f "usebackq delims=" %%S in (`powershell -NoProfile -Command ^
  "$n=$env:TS_PS_NAME; $safe=$n -replace '[^\p{L}\p{N}\.\-_ ]','_'; $safe" 2^>nul`) do (
  if not defined SAFE set "SAFE=%%S"
)
if not defined SAFE set "SAFE=%BASE%"
for %%B in ("%SAFE%") do set "SAFE_BASE=%%~nB"
call "%RUN_BAT%" run --input "%SRC%" %EXTRA_ARGS%
if errorlevel 1 (
  echo [ERROR] ASR en echec pour %SRC%
  set "RUN_FAILED=1"
  goto :HandleResult
)
call :ResolveWorkDir "%BASE%"
if not defined WORK_DIR (
  echo [ERROR] work/%BASE% introuvable.
  set "RUN_FAILED=1"
  goto :HandleResult
)
call :ResolveExportDir "%BASE%" "%SRC_DIR%"
if not defined EXPORT_DIR (
  echo [ERROR] Exports introuvables pour %BASE%.
  set "RUN_FAILED=1"
  goto :HandleResult
)
call :StageOutputs "%SAFE_BASE%" "%BASE%"
if errorlevel 1 (
  echo [ERROR] Impossible de copier les artefacts pour %BASE%.
  set "RUN_FAILED=1"
)
:HandleResult
if /I "%SRC_KIND%"=="video" (
  set "DEST_OK=%VIDEO_PROCESSED%"
  set "DEST_FAIL=%VIDEO_FAILED%"
) else (
  set "DEST_OK=%AUDIO_PROCESSED%"
  set "DEST_FAIL=%AUDIO_FAILED%"
)
if "%RUN_FAILED%"=="0" (
  call :MoveToState "%SRC%" "%DEST_OK%"
  if errorlevel 1 (
    echo [WARN] Impossible de deplacer le media vers "%DEST_OK%".
  )
  set /a FILES_OK+=1
) else (
  call :MoveToState "%SRC%" "%DEST_FAIL%"
  if errorlevel 1 (
    echo [WARN] Impossible de deplacer le media vers "%DEST_FAIL%".
  )
  set /a FILES_FAIL+=1
)
exit /b 0
:ResolveWorkDir
set "TARGET=%~1"
set "WORK_DIR=%REPO_ROOT%\work\%TARGET%"
if exist "%WORK_DIR%" (
  exit /b 0
)
for /d %%W in ("%REPO_ROOT%\work\*") do (
  if /I "%%~nxW"=="%TARGET%" (
    set "WORK_DIR=%%~fW"
    exit /b 0
  )
)
set "WORK_DIR="
exit /b 1
:ResolveExportDir
set "BASE_NAME=%~1"
set "SRC_PARENT=%~2"
set "EXPORT_DIR="
set "MANIFEST_PATH=%REPO_ROOT%\work\%BASE_NAME%\logs\run_manifest.json"
if exist "%MANIFEST_PATH%" (
  set "TS_MANIFEST=%MANIFEST_PATH%"
  for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command ^
    "$p=$env:TS_MANIFEST; try{$d=Get-Content -Raw -LiteralPath $p | ConvertFrom-Json}catch{$d=$null}; if($d -and $d.export_dir){$d.export_dir}" 2^>nul`) do (
      if not defined EXPORT_DIR set "EXPORT_DIR=%%D"
  )
)
if defined EXPORT_DIR if not exist "%EXPORT_DIR%" set "EXPORT_DIR="
if not defined EXPORT_DIR (
  set "EXPORT_DIR=%SRC_PARENT%TRANSCRIPT - %BASE_NAME%"
)
exit /b 0
:StageOutputs
set "SAFE_NAME=%~1"
set "DOC_NAME=%~2"
set "DOC_STAGE=%OUTPUT_SOURCE%\%SAFE_NAME%"
set "TARGET_WORK=%DOC_STAGE%\work"
set "TARGET_TRANS=%DOC_STAGE%\TRANSCRIPT - %DOC_NAME%"
if exist "%DOC_STAGE%" rd /s /q "%DOC_STAGE%" >nul 2>&1
call :EnsureDir "%TARGET_WORK%" || exit /b 1
robocopy "%WORK_DIR%" "%TARGET_WORK%\%DOC_NAME%" /MIR /NFL /NDL /NJH /NJS >nul
if errorlevel 8 exit /b 1
call :EnsureDir "%TARGET_TRANS%" || exit /b 1
robocopy "%EXPORT_DIR%" "%TARGET_TRANS%" /E /NFL /NDL /NJH /NJS >nul
if errorlevel 8 exit /b 1
echo [OK] Artefacts centralisés dans %DOC_STAGE%
exit /b 0
:EnsureDir
set "TARGET_DIR=%~1"
if "%TARGET_DIR%"=="" exit /b 1
if exist "%TARGET_DIR%" exit /b 0
mkdir "%TARGET_DIR%" >nul 2>&1
exit /b %ERRORLEVEL%
:MoveToState
REM Usage: call :MoveToState "C:\path\file.mp4" "\\server\share\_processed"
set "SRC=%~1"
set "DEST_DIR=%~2"
if "%SRC%"=="" exit /b 1
if "%DEST_DIR%"=="" exit /b 1
if not exist "%DEST_DIR%" mkdir "%DEST_DIR%" >nul 2>&1
move /Y "%SRC%" "%DEST_DIR%\" >nul
if errorlevel 1 exit /b 1
exit /b 0
:FAIL
echo.
echo ECHEC pipeline_asr_batch
exit /b 1
