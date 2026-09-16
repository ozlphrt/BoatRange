@echo off
REM Sets up (first run only) and launches the Axopar range planner:
REM FastAPI backend on :8000, Vite frontend on :2343. See README.md "Quick
REM Start" for what this mirrors and MapTiler API key setup for a real basemap.
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==^> Checking Python virtualenvs...

for %%D in (packages\fuel-model apps\api services\routing services\marine-data services\isochrone services\diagnostics) do (
    if not exist "%%D\.venv" (
        echo     creating venv + installing %%D
        python -m venv "%%D\.venv"
        call "%%D\.venv\Scripts\pip.exe" install -e "%%D" -e "%%D[dev]" -q
        if errorlevel 1 goto :error
    )
)

REM apps\api imports the other four packages directly, so it needs them
REM installed into ITS OWN venv too (see apps/api/app/main.py).
if not exist "apps\api\.venv\.deps_linked_v2" (
    echo     linking sibling packages into apps\api's venv
    call apps\api\.venv\Scripts\pip.exe install -e packages\fuel-model -e services\routing -e services\marine-data -e services\isochrone -e services\diagnostics -q
    if errorlevel 1 goto :error
    type nul > "apps\api\.venv\.deps_linked_v2"
)

echo ==^> Checking frontend dependencies...
if not exist "node_modules" (
    call npm install
    if errorlevel 1 goto :error
)

echo ==^> Starting API (http://localhost:8000)...
start "Axopar API" cmd /k "apps\api\.venv\Scripts\uvicorn.exe app.main:app --reload --port 8000 --app-dir apps\api"

echo ==^> Starting web app (http://localhost:2343)...
start "Axopar Web" cmd /k "npm --prefix apps\web run dev"

echo.
echo Open http://localhost:2343 in your browser.
echo Two new windows were opened (API and Web) — close them to stop the servers.
goto :eof

:error
echo.
echo Setup failed — see the error above.
exit /b 1
