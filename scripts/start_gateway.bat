@echo off
setlocal

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
chcp 65001 >nul

cd /d D:\Work\nano-agentx

if not exist logs mkdir logs

echo [%date% %time%] Starting nanobot gateway... >> "D:\Work\nano-agentx\logs\gateway.log"
"D:\Development\Python\Envs\nano-bot\Scripts\nanobot.exe" gateway >> "D:\Work\nano-agentx\logs\gateway.log" 2>&1
