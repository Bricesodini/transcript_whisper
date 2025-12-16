@echo off
setlocal
set "SCRIPT_DIR=%~dp0"

call :SetupCudaPath

powershell -NoLogo -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run.ps1" %*
exit /b %ERRORLEVEL%

:SetupCudaPath
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
for %%I in ("%REPO_ROOT%\..") do set "WORKSPACE_ROOT=%%~fI"

if defined TS_VENV_DIR (
    set "VENV_ROOT=%TS_VENV_DIR%"
) else (
    set "VENV_ROOT=%WORKSPACE_ROOT%\.venv"
)

set "SITE_PACKAGES="
call :FindSite "%VENV_ROOT%\Lib\site-packages"
call :FindSite "%VENV_ROOT%\lib\site-packages"
call :FindSite "%VENV_ROOT%\site-packages"

if not defined SITE_PACKAGES goto :EOF

for %%D in (cublas cudnn cuda_runtime) do (
    if exist "%SITE_PACKAGES%\nvidia\%%D\bin" (
        call :PrependPath "%SITE_PACKAGES%\nvidia\%%D\bin"
    )
)
goto :EOF

:FindSite
if defined SITE_PACKAGES goto :EOF
set "CANDIDATE=%~1"
if exist "%CANDIDATE%" (
    set "SITE_PACKAGES=%CANDIDATE%"
)
goto :EOF

:PrependPath
set "DIR=%~1"
if "%DIR%"=="" goto :EOF
set "PATH=%DIR%;%PATH%"
goto :EOF
