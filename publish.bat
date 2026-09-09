@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PY=C:\Users\KIM\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"
echo.
echo  *** PUBLISH: encrypt data/live -> docs/data/bundle.enc -> push ***
echo  Only users in docs/users.json can decrypt. Cancel keeps current site.
echo.
set /p ok="Type YES to publish: "
if /I not "%ok%"=="YES" ( echo canceled. & exit /b 1 )
"%PY%" "scripts\manage_users.py" pack
if errorlevel 1 ( echo pack failed. & exit /b 1 )
git add docs/data/bundle.enc docs/users.json
git commit -m "chore: update dashboard data"
git push
echo Done. The site updates in 1-2 minutes.
endlocal
