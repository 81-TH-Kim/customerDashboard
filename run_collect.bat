@echo off
REM RPA dashboard - daily mail collector (Windows Task Scheduler)
REM Scheduled daily at 07:30 and 09:30 (RPA mail sometimes arrives after 08:00).
REM Writes data\live\*.json. Log: data\_schedule.log
REM To publish to the public site afterwards, run publish.bat.
chcp 65001 >nul
cd /d "%~dp0"
set "PY=C:\Users\KIM\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"
echo ===== %date% %time% ===== >> "data\_schedule.log"
"%PY%" "scripts\collect.py" --days 5 >> "data\_schedule.log" 2>&1
