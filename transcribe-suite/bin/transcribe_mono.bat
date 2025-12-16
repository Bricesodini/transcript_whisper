@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "NO_TK=1"

REM === PATHS =========================================================
set "NET_IN=\\bricesodini\Savoirs\Transcriptions\input"
set "NET_OUT=\\bricesodini\Savoirs\Transcriptions\output"

set "BIN_DIR=%~dp0"
set "RUN_BAT=%BIN_DIR%run.bat"
for %%I in ("%BIN_DIR%..") do set "ROOT=%%~fI"

REM === SANITY CHECKS ==================================================
if not exist "%RUN_BAT%" (echo ERROR: run.bat introuvable & goto FAIL)
if not exist "%NET_IN%" (echo ERROR: input introuvable & goto FAIL)
if not exist "%NET_OUT%" (echo ERROR: output introuvable & goto FAIL)

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
pause
exit /b 0

REM ===================================================================
REM ======================== PROCESS ONE FILE =========================
REM ===================================================================
:ProcessOne
set "SRC=%~1"
echo.
echo === Traitement ===
echo %SRC%

for %%Z in ("%SRC%") do (
  set "NAME=%%~nxZ"
  set "BASE=%%~nZ"
  set "SRC_DIR=%%~dpZ"
)

REM --- SAFE BASE NAME FOR OUTPUT FOLDER (ROBUST APOSTROPHES/UNICODE) --
set "TS_PS_NAME=%NAME%"
for /f "usebackq delims=" %%S in (`
  powershell -NoProfile -Command ^
  "$n=$env:TS_PS_NAME; $s=$n -replace '[^\p{L}\p{N}\.\-_ ]','_'; $s"
  2^>nul
`) do set "SAFE=%%S"
for %%B in ("%SAFE%") do set "SAFE_BASE=%%~nB"

REM --- RUN PIPELINE --------------------------------------------------
call "%RUN_BAT%" run ^
  --config "%ROOT%\configs\base_stable.yaml" ^
  --asr-workers 2 ^
  --input "%SRC%" ^
  --mode mono ^
  --chunk-length 20 ^
  --no-vad ^
  --skip-diarization ^
  --polish-outputs ^
  --fail-fast ^
  --no-partial-export ^
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
    powershell -NoProfile -Command ^
    "$p=$env:TS_MANIFEST; try{$d=Get-Content -Raw -LiteralPath $p | ConvertFrom-Json}catch{$d=$null}; if($d -and $d.export_dir){$d.export_dir}"
    2^>nul
  `) do if not defined EXPORT_DIR set "EXPORT_DIR=%%D"
)

REM --- VALIDATE EXPORT_DIR (avoid capturing PS error text) -----------
if defined EXPORT_DIR (
  if not exist "%EXPORT_DIR%" set "EXPORT_DIR="
)

REM --- FALLBACK (CERTAIN) --------------------------------------------
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

REM --- MOVE EXPORTS (ROBUST SMB) -------------------------------------
if exist "%FINAL_EXPORTS%" rd /s /q "%FINAL_EXPORTS%" >nul 2>&1
call :MoveDirRobust "%EXPORT_DIR%" "%FINAL_EXPORTS%"
if errorlevel 1 (
  echo ERROR: impossible de déplacer les exports
  exit /b 1
)
echo [OK] Exports déplacés vers "%FINAL_EXPORTS%"

REM --- COPY LOGS (BEST EFFORT) ---------------------------------------
set "SOURCE_LOGS=%ROOT%\work\%BASE%\logs"
if exist "%SOURCE_LOGS%" (
  robocopy "%SOURCE_LOGS%" "%FINAL_LOGS%" /E /NFL /NDL /NJH /NJS >nul
)

REM --- MOVE SOURCE MEDIA (VIDE INPUT) --------------------------------
move /Y "%SRC%" "%FINAL_MEDIA%\%NAME%" >nul
if errorlevel 1 (
  echo ERROR: impossible de déplacer le media source
  exit /b 1
)
echo [OK] Media déplacé vers "%FINAL_MEDIA%\%NAME%"

exit /b 0

REM ===================================================================
REM ======================== ROBUST MOVE DIR ==========================
REM ===================================================================
:MoveDirRobust
set "MOVE_SRC=%~1"
set "MOVE_DEST=%~2"

if "%MOVE_SRC%"=="" exit /b 1
if "%MOVE_DEST%"=="" exit /b 1

robocopy "%MOVE_SRC%" "%MOVE_DEST%" /MOVE /E /NFL /NDL /NJH /NJS >nul
set "RC=%ERRORLEVEL%"

REM robocopy success codes: 0–7
if %RC% LEQ 7 (
  rd "%MOVE_SRC%" 2>nul
  exit /b 0
)

exit /b 1

REM ===================================================================
:FAIL
echo.
echo ECHEC
pause
exit /b 1
