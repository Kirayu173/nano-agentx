@echo off
setlocal

set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "WRAPPER_VBS=%~dp0start_gateway_hidden.vbs"
set "GATEWAY_LOG=%ROOT%\logs\gateway.log"
set "NANOBOT_EXE=D:\Development\Python\Envs\nano-bot\Scripts\nanobot.exe"

if /I not "%~1"=="--hidden-run" (
  if exist "%WRAPPER_VBS%" (
    wscript //nologo "%WRAPPER_VBS%" >nul 2>&1
    if not errorlevel 1 exit /b 0
  )
)

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
chcp 65001 >nul

cd /d "%ROOT%"

if not exist logs mkdir logs

if not exist "%NANOBOT_EXE%" (
  for /f "delims=" %%I in ('where nanobot 2^>nul') do (
    set "NANOBOT_EXE=%%~fI"
    goto :run_gateway
  )
)

:run_gateway
echo [%date% %time%] Starting nanobot gateway... >> "%GATEWAY_LOG%"
"%NANOBOT_EXE%" gateway >> "%GATEWAY_LOG%" 2>&1
