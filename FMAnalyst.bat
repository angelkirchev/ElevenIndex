@echo off
rem Starts FMAnalyst. Double-click this file (or a shortcut to it).
cd /d "%~dp0"
where python >nul 2>nul || (
  echo Python was not found. Install it from python.org and tick "Add python.exe to PATH".
  pause
  exit /b 1
)
python -c "import webview" 2>nul || (
  echo Installing pywebview, the part that shows the pages...
  python -m pip install pywebview || (pause & exit /b 1)
)
start "" pythonw app.py
