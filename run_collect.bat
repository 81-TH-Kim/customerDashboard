@echo off
REM RPA dashboard - daily mail collector + auto publish (Windows Task Scheduler)
REM Scheduled daily at 07:30 and 09:30 (RPA mail sometimes arrives after 08:00).
REM 1) collect.py: mail -> data\live\*.json
REM 2) auto_publish.py: encrypt + git push to GitHub Pages (only if bundle.enc changed)
REM Log: data\_schedule.log
chcp 65001 >nul
cd /d "%~dp0"
set "PY=C:\Users\KIM\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"
echo ===== %date% %time% ===== >> "data\_schedule.log"
"%PY%" "scripts\collect.py" --days 5 >> "data\_schedule.log" 2>&1
"%PY%" "scripts\auto_publish.py" >> "data\_schedule.log" 2>&1
