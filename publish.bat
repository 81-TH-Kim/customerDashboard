@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo.
echo  *** PUBLISH: data\live\*.json  ->  GitHub Pages (PUBLIC site) ***
echo  Anyone can view the published data. Sample data stays if you cancel.
echo.
set /p ok="Type YES to publish: "
if /I not "%ok%"=="YES" ( echo canceled. & exit /b 1 )
copy /Y "data\live\inflow.json"         "docs\data\inflow.json"          >nul
copy /Y "data\live\channels.json"       "docs\data\channels.json"        >nul
copy /Y "data\live\collection_log.json" "docs\data\collection_log.json"  >nul
git add docs/data
git commit -m "chore: update dashboard data"
git push
echo Done. The site updates in 1-2 minutes.
endlocal
