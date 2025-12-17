@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
echo [DEBUG] Batch OK - setlocal fonctionne.
set "PIPELINE_ROOT=\\bricesodini\Savoirs\03_data_pipeline"
set "STAGING_ASR=%PIPELINE_ROOT%\02_output_source\asr"
set "STAGING_PDF=%PIPELINE_ROOT%\02_output_source\pdf"
set "RAG_OUTPUT=%PIPELINE_ROOT%\03_output_RAG"
set "BIN_DIR=%~dp0"
set "RUN_BAT=%BIN_DIR%run.bat"
for %%I in ("%BIN_DIR%..") do set "REPO_ROOT=%%~fI"
if not exist "%RUN_BAT%" (
  echo [ERROR] run.bat introuvable.
  goto FAIL
)
set "DATA_PIPELINE_ROOT=%PIPELINE_ROOT%"
set "SCRIPT_QUERY="
set "EXTRA_ARGS="
set "VERSION_TAG="
set "USER_DOC_ID="
set "LEXICON_SCAN=0"
:ParseArgs
if "%~1"=="" goto ArgsDone
if /I "%~1"=="--query" (
  shift
  if "%~1"=="" goto ArgsDone
  set "SCRIPT_QUERY=%~1"
  shift
  goto ParseArgs
)
if /I "%~1"=="--version-tag" (
  shift
  if "%~1"=="" goto ArgsDone
  set "VERSION_TAG=%~1"
  set "EXTRA_ARGS=%EXTRA_ARGS% --version-tag %VERSION_TAG%"
  shift
  goto ParseArgs
)
if /I "%~1"=="--doc-id" (
  shift
  if "%~1"=="" goto ArgsDone
  set "USER_DOC_ID=%~1"
  set "EXTRA_ARGS=%EXTRA_ARGS% --doc-id %USER_DOC_ID%"
  shift
  goto ParseArgs
)
if /I "%~1"=="--lexicon-scan" (
  set "LEXICON_SCAN=1"
  shift
  goto ParseArgs
)
set "EXTRA_ARGS=%EXTRA_ARGS% %~1"
shift
goto ParseArgs
:ArgsDone
call :EnsureDir "%RAG_OUTPUT%"
if errorlevel 1 goto FAIL
call :EnsureDir "%STAGING_PDF%"
if errorlevel 1 goto FAIL
set /a DOC_OK=0
set /a DOC_FAIL=0
for /d %%D in ("%STAGING_ASR%\*") do (
  call :ProcessDoc "%%D"
)
echo.
echo === Pipeline RAG terminée ===
echo OK   : %DOC_OK%
echo FAIL : %DOC_FAIL%
if %DOC_FAIL% gtr 0 (
  exit /b 1
) else (
  exit /b 0
)
:ProcessDoc
set "DOC_STAGE=%~1"
if not exist "%DOC_STAGE%" goto :EOF
set "WORK_PARENT=%DOC_STAGE%\work"
if not exist "%WORK_PARENT%" (
  echo [WARN] Aucun dossier work pour %DOC_STAGE%
  goto :EOF
)
set "WORK_DIR="
for /d %%W in ("%WORK_PARENT%\*") do (
  if not defined WORK_DIR set "WORK_DIR=%%~fW"
)
if not defined WORK_DIR (
  echo [WARN] Aucun sous-dossier dans %WORK_PARENT%
  goto :EOF
)
for %%C in ("%DOC_STAGE%") do set "DOC_STAGE_NAME=%%~nxC"
set "DOC_ID=%USER_DOC_ID%"
if not defined DOC_ID (
  set "TS_DOC_STAGE=%DOC_STAGE_NAME%"
  for /f "usebackq delims=" %%S in (`powershell -NoProfile -Command ^
    "$n=$env:TS_DOC_STAGE;" ^
    "$formD=$n.Normalize([System.Text.NormalizationForm]::FormD);" ^
    "$sb=New-Object System.Text.StringBuilder;" ^
    "foreach ($ch in $formD.ToCharArray()) { $cat=[System.Globalization.CharUnicodeInfo]::GetUnicodeCategory($ch); if ($cat -ne [System.Globalization.UnicodeCategory]::NonSpacingMark) { [void]$sb.Append($ch) } }" ^
    "$ascii=$sb.ToString().Normalize([System.Text.NormalizationForm]::FormC);" ^
    "$slug=$ascii.ToLowerInvariant() -replace '[^a-z0-9]+','-';" ^
    "$slug=$slug.Trim('-');" ^
    "if ([string]::IsNullOrWhiteSpace($slug)) { $slug='doc' }" ^
    "$slug" 2^>nul`) do (
      if not defined DOC_ID set "DOC_ID=%%S"
  )
  if not defined DOC_ID set "DOC_ID=%DOC_STAGE_NAME%"
)
set "RUN_ARGS=%EXTRA_ARGS%"
if not defined USER_DOC_ID (
  set "RUN_ARGS=%RUN_ARGS% --doc-id \"%DOC_ID%\""
)
echo.
echo === rag-export pour %WORK_DIR% ===
if "%LEXICON_SCAN%"=="1" (
  call "%RUN_BAT%" rag lexicon scan --input "%WORK_DIR%"
  if errorlevel 1 (
    echo [WARN] Lexicon scan a échoué pour %WORK_DIR%
  )
)
call "%RUN_BAT%" rag --input "%WORK_DIR%" %RUN_ARGS% --force
if errorlevel 1 (
  echo [ERROR] rag-export a échoué pour %WORK_DIR%
  set /a DOC_FAIL+=1
  goto :EOF
)
set "RAG_DOC_DIR="
call :ResolveRagDir "%DOC_ID%"
if errorlevel 1 (
  echo [ERROR] Impossible de localiser la sortie RAG pour %DOC_ID%.
  set /a DOC_FAIL+=1
  goto :EOF
)
call "%RUN_BAT%" rag doctor --input "%RAG_DOC_DIR%"
if errorlevel 1 (
  echo [ERROR] rag doctor a échoué pour %DOC_ID%
  set /a DOC_FAIL+=1
  goto :EOF
)
if defined SCRIPT_QUERY (
  call "%RUN_BAT%" rag query --input "%RAG_DOC_DIR%" --query "%SCRIPT_QUERY%" --top-k 5
  if errorlevel 1 (
    echo [WARN] rag query sans résultat pour %DOC_ID%
  )
)
set /a DOC_OK+=1
goto :EOF
:EnsureDir
set "TARGET_DIR=%~1"
if "%TARGET_DIR%"=="" exit /b 1
if exist "%TARGET_DIR%" exit /b 0
mkdir "%TARGET_DIR%" >nul 2>&1
exit /b %ERRORLEVEL%
:ResolveRagDir
set "TARGET_DOC=%~1"
set "RAG_DOC_DIR="
set "DOC_PARENT=%RAG_OUTPUT%\RAG-%TARGET_DOC%"
if not exist "%DOC_PARENT%" exit /b 1
if defined VERSION_TAG (
  if exist "%DOC_PARENT%\%VERSION_TAG%" (
    set "RAG_DOC_DIR=%DOC_PARENT%\%VERSION_TAG%"
    exit /b 0
  ) else (
    exit /b 1
  )
)
for /f "delims=" %%R in ('dir /b /ad /o-d "%DOC_PARENT%" 2^>nul') do (
  if not defined RAG_DOC_DIR set "RAG_DOC_DIR=%DOC_PARENT%\%%R"
)
if not defined RAG_DOC_DIR exit /b 1
exit /b 0
:FAIL
echo.
echo ECHEC pipeline_rag_batch
exit /b 1
