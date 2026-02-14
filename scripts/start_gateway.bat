@echo off
setlocal EnableExtensions

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
call :load_env "%ROOT%\.env"

if not exist logs mkdir logs

if not exist "%NANOBOT_EXE%" (
  for /f "delims=" %%I in ('where nanobot 2^>nul') do (
    set "NANOBOT_EXE=%%~fI"
    goto :run_gateway
  )
)

:run_gateway
echo [%date% %time%] Starting nanobot gateway... >> "%GATEWAY_LOG%"
if defined ENV_LOADED_COUNT echo [%date% %time%] Loaded %ENV_LOADED_COUNT% vars from .env >> "%GATEWAY_LOG%"
"%NANOBOT_EXE%" gateway >> "%GATEWAY_LOG%" 2>&1
goto :eof

:load_env
set "ENV_FILE=%~1"
set "ENV_LOADED_COUNT=0"
if not exist "%ENV_FILE%" goto :eof

for /f "usebackq delims=" %%L in ("%ENV_FILE%") do call :load_env_line "%%L"

goto :eof

:load_env_line
set "LINE=%~1"
if not defined LINE goto :eof

for /f "tokens=* delims= " %%A in ("%LINE%") do set "LINE=%%~A"
if "%LINE%"=="" goto :eof
if "%LINE:~0,1%"=="#" goto :eof
if /I "%LINE:~0,7%"=="export " set "LINE=%LINE:~7%"

set "KEY="
set "VAL="
for /f "tokens=1* delims==" %%K in ("%LINE%") do (
  set "KEY=%%~K"
  set "VAL=%%~L"
)

if not defined KEY goto :eof
for /f "tokens=* delims= " %%A in ("%KEY%") do set "KEY=%%~A"
for /f "tokens=* delims= " %%A in ("%VAL%") do set "VAL=%%~A"
if "%KEY%"=="" goto :eof

if defined VAL (
  if "%VAL:~0,1%"=="\"" if "%VAL:~-1%"=="\"" set "VAL=%VAL:~1,-1%"
  if "%VAL:~0,1%"=="'" if "%VAL:~-1%"=="'" set "VAL=%VAL:~1,-1%"
)

set "%KEY%=%VAL%"
set /a ENV_LOADED_COUNT+=1
goto :eof
