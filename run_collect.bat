@echo off
REM RPA dashboard - daily mail collector (Windows Task Scheduler)
REM Scheduled daily 07:30. Writes data\live\*.json. Log: data\_schedule.log
REM To publish to the public site afterwards, run publish.bat.
chcp 65001 >nul
cd /d "%~dp0"
set "PY=C:\Users\KIM\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"
echo ===== %date% %time% ===== >> "data\_schedule.log"
"%PY%" "scripts\collect.py" --days 3 >> "data\_schedule.log" 2>&1
