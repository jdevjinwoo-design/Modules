@echo off
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

rem ---- find a real Python (the Microsoft Store stub does not count)
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY (
  echo [ERROR] Python is not installed.
  echo Install it from the page that opens now.
  echo On the first install screen, CHECK "Add python.exe to PATH".
  echo Then run this file again.
  start "" https://www.python.org/downloads/
  pause
  exit /b 1
)
%PY% --version

rem ---- photo folder: dragged onto this file, or typed/dragged into the window
set "SRC=%~1"
if not defined SRC (
  echo.
  echo Drag your PHOTO FOLDER into this window, then press Enter:
  set /p "SRC="
)
if not defined SRC (
  echo [ERROR] No folder given.
  pause
  exit /b 1
)
set "SRC=%SRC:"=%"
if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"
if not exist "%SRC%\" (
  echo [ERROR] Folder not found: "%SRC%"
  pause
  exit /b 1
)

echo.
echo Installing required packages (first run takes 1-2 minutes)...
%PY% -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Package install failed. Send a screenshot of this window.
  pause
  exit /b 1
)

echo.
%PY% wmremove.py "%SRC%" "%SRC%_cleaned"
echo.
if errorlevel 1 (
  echo [ERROR] Something went wrong. Send a screenshot of this window.
) else (
  echo DONE. Results: "%SRC%_cleaned"
  start "" "%SRC%_cleaned"
)
pause
