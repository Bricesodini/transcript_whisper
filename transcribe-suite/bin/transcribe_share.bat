@echo off
setlocal enabledelayedexpansion

REM === Auto-localisation du script ===
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

echo === Transcribe Suite Worker ===
echo Script directory: %SCRIPT_DIR%
echo Working directory: %CD%

set "NO_TK=1"
if not defined TS_ALLOW_LOCAL_DATA set "TS_ALLOW_LOCAL_DATA=1"

REM === PATHS =========================================================
set "NET_IN=\\bricesodini\Savoirs\Transcriptions\input"
set "NET_OUT=\\bricesodini\Savoirs\Transcriptions\output"
set "POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "ROBOCOPY=%SystemRoot%\System32\robocopy.exe"

set "BIN_DIR=%SCRIPT_DIR%"
set "RUN_BAT=%BIN_DIR%run.bat"
for %%I in ("%BIN_DIR%..") do set "ROOT=%%~fI"

REM === SANITY CHECKS ==================================================
if not exist "%RUN_BAT%" (echo ERROR: run.bat introuvable & goto FAIL)
if not exist "%NET_IN%" (echo ERROR: input introuvable & goto FAIL)
if not exist "%NET_OUT%" (echo ERROR: output introuvable & goto FAIL)
if not exist "%POWERSHELL%" (echo ERROR: powershell introuvable & goto FAIL)
if not exist "%ROBOCOPY%" (echo ERROR: robocopy introuvable & goto FAIL)

REM === LOOP ON INPUT FILES ===========================================
set "FILES_DONE=0"
for %%E in (mp4 wav mp3 m4a) do (
  for /f "delims=" %%F in ('dir /b /a:-d "%NET_IN%\*.%%E" 2^>nul') do (
    call :ProcessOne "%NET_IN%\%%F"
    if errorlevel 1 goto FAIL
    set /a FILES_DONE+=1
  )
)

echo.
echo Terminé : !FILES_DONE! fichier(s).
exit /b 0

REM ===================================================================
REM ======================== PROCESS ONE FILE =========================
REM ===================================================================
:ProcessOne
set "SRC=%~1"
echo.
echo === Traitement (share/talkshow) ===
echo %SRC%
echo [%DATE% %TIME%] Debut transcription : %SRC%

for %%Z in ("%SRC%") do (
  set "NAME=%%~nxZ"
  set "BASE=%%~nZ"
  set "SRC_DIR=%%~dpZ"
)

set "TS_PS_NAME=%NAME%"
for /f "usebackq delims=" %%S in (`
  "%POWERSHELL%" -NoProfile -Command ^
  "$n=$env:TS_PS_NAME; $s=$n -replace '[^\p{L}\p{N}\.\-_ ]','_'; $s"
  2^>nul
`) do set "SAFE=%%S"
for %%B in ("%SAFE%") do set "SAFE_BASE=%%~nB"

call "%RUN_BAT%" run ^
  --config "%ROOT%\configs\base_stable.yaml" ^
  --input "%SRC%" ^
  --lang auto ^
  --profile talkshow ^
  --export md,json,vtt

if errorlevel 1 (
  echo ERROR: pipeline a échoué pour "%SRC%"
  exit /b 1
)

REM --- RESOLVE EXPORT DIR --------------------------------------------
set "EXPORT_DIR="
set "MANIFEST_PATH=%ROOT%\work\%BASE%\logs\run_manifest.json"

if exist "%MANIFEST_PATH%" (
  set "TS_MANIFEST=%MANIFEST_PATH%"
  for /f "usebackq delims=" %%D in (`
    "%POWERSHELL%" -NoProfile -Command ^
    "$p=$env:TS_MANIFEST; try{$d=Get-Content -Raw -LiteralPath $p | ConvertFrom-Json}catch{$d=$null}; if($d -and $d.export_dir){$d.export_dir}"
    2^>nul
  `) do if not defined EXPORT_DIR set "EXPORT_DIR=%%D"
)

if defined EXPORT_DIR (
  if not exist "%EXPORT_DIR%" set "EXPORT_DIR="
)

if not defined EXPORT_DIR (
  set "EXPORT_DIR=%SRC_DIR%TRANSCRIPT - %BASE%"
)

echo [INFO] EXPORT_DIR = "%EXPORT_DIR%"

if not exist "%EXPORT_DIR%" (
  echo ERROR: dossier exports introuvable "%EXPORT_DIR%"
  exit /b 1
)

REM --- BUILD OUTPUT STRUCTURE ----------------------------------------
set "FINAL_ROOT=%NET_OUT%\%SAFE_BASE%"
set "FINAL_EXPORTS=%FINAL_ROOT%\TRANSCRIPT - %BASE%"
set "FINAL_LOGS=%FINAL_ROOT%\logs"
set "FINAL_MEDIA=%FINAL_ROOT%\source"

if not exist "%FINAL_ROOT%" mkdir "%FINAL_ROOT%" >nul 2>&1
if not exist "%FINAL_LOGS%" mkdir "%FINAL_LOGS%" >nul 2>&1
if not exist "%FINAL_MEDIA%" mkdir "%FINAL_MEDIA%" >nul 2>&1

if exist "%FINAL_EXPORTS%" rd /s /q "%FINAL_EXPORTS%" >nul 2>&1
call :MoveDirRobust "%EXPORT_DIR%" "%FINAL_EXPORTS%"
if errorlevel 1 (
  echo ERROR: impossible de déplacer les exports
  exit /b 1
)
echo [OK] Exports déplacés vers "%FINAL_EXPORTS%"

set "SOURCE_LOGS=%ROOT%\work\%BASE%\logs"
if exist "%SOURCE_LOGS%" (
  "%ROBOCOPY%" "%SOURCE_LOGS%" "%FINAL_LOGS%" /E /NFL /NDL /NJH /NJS >nul
)

move /Y "%SRC%" "%FINAL_MEDIA%\%NAME%" >nul
if errorlevel 1 (
  echo ERROR: impossible de déplacer le media source
  exit /b 1
)
echo [OK] Media déplacé vers "%FINAL_MEDIA%\%NAME%"

echo [%DATE% %TIME%] Fin traitement
exit /b 0

REM ===================================================================
REM ======================== ROBUST MOVE DIR ==========================
REM ===================================================================
:MoveDirRobust
set "MOVE_SRC=%~1"
set "MOVE_DEST=%~2"

if "%MOVE_SRC%"=="" exit /b 1
if "%MOVE_DEST%"=="" exit /b 1

"%ROBOCOPY%" "%MOVE_SRC%" "%MOVE_DEST%" /MOVE /E /NFL /NDL /NJH /NJS >nul
set "RC=%ERRORLEVEL%"
if %RC% LEQ 7 (
  rd "%MOVE_SRC%" 2>nul
  exit /b 0
)
exit /b 1

REM ===================================================================
:FAIL
echo.
echo ECHEC
exit /b 1
