@echo off
setlocal

set "ROOT=D:\Work\nano-agentx"
set "WRAPPER_VBS=%~dp0start_gateway_hidden.vbs"
set "GATEWAY_LOG=%ROOT%\logs\gateway.log"

if /I not "%~1"=="--hidden-run" (
  if exist "%WRAPPER_VBS%" (
    cscript //nologo "%WRAPPER_VBS%" >nul 2>&1
    if not errorlevel 1 exit /b 0
  )
)

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
chcp 65001 >nul

cd /d "%ROOT%"

if not exist logs mkdir logs

echo [%date% %time%] Starting nanobot gateway... >> "%GATEWAY_LOG%"
"D:\Development\Python\Envs\nano-bot\Scripts\nanobot.exe" gateway >> "%GATEWAY_LOG%" 2>&1
