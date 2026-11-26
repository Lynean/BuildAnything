@echo off
rem Starts BuildAnything: the backend and the app, both on this computer only, then opens it in your browser.
rem The first run sets everything up (needs uv, Node.js and npm). Close the two windows it opens to stop it.
setlocal
cd /d "%~dp0"

where uv >nul 2>nul || (echo uv isn't installed. Get it from https://docs.astral.sh/uv/ & pause & exit /b 1)
where npm >nul 2>nul || (echo Node.js isn't installed. Get it from https://nodejs.org/ & pause & exit /b 1)

if not exist "backend\.venv\Scripts\python.exe" (
  echo Setting up the backend, first run only...
  pushd backend
  call uv sync || (popd & echo The backend setup failed. & pause & exit /b 1)
  popd
)

if not exist "app\node_modules" (
  echo Installing the app's packages, first run only...
  pushd app
  call npm install || (popd & echo npm install failed. & pause & exit /b 1)
  popd
)

echo Building the app...
pushd app
call npx vite build --logLevel warn || (popd & echo The app build failed. & pause & exit /b 1)
popd

netstat -ano | findstr /r /c:":8787 .*LISTENING" >nul
if errorlevel 1 (
  start "BuildAnything backend" /D "%~dp0backend" .venv\Scripts\python.exe -m uvicorn buildanything.api:app --host 127.0.0.1 --port 8787
) else (
  echo The backend is already running on port 8787.
)

netstat -ano | findstr /r /c:":4173 .*LISTENING" >nul
if errorlevel 1 (
  start "BuildAnything app" /D "%~dp0app" cmd /c npx vite preview --port 4173 --host 127.0.0.1 --strictPort
) else (
  echo The app is already running on port 4173.
)

echo Waiting for the backend...
for /l %%i in (1,1,30) do (
  powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8787/api/projects | Out-Null; exit 0 } catch { exit 1 }" && goto ready
  timeout /t 1 >nul
)
echo The backend didn't answer after 30 seconds. Check the "BuildAnything backend" window for the error.
pause
exit /b 1

:ready
start "" http://127.0.0.1:4173
echo BuildAnything is running at http://127.0.0.1:4173
echo To stop it, close the "BuildAnything backend" and "BuildAnything app" windows.
