@echo off
rem ============================================================
rem  Builds ShopDesk-Setup.exe. Double-click this file.
rem  Needs (once): Python 3.12 from python.org ("Add python.exe to PATH" ticked) and the free Inno Setup 6.
rem ============================================================
setlocal
cd /d "%~dp0"
echo.
echo  ===== ShopDesk installer builder =====
echo.

set "PY="
for %%V in (3.12 3.13 3.11 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo  Python was not found. Install Python 3.12 from https://www.python.org/downloads/
  echo  and tick "Add python.exe to PATH" in the first screen, then run this file again.
  goto :fail
)
echo  Using: %PY%

echo.
echo  [1/4] Preparing the build environment (first time takes a few minutes)...
if not exist ".build_venv\Scripts\python.exe" (
  %PY% -m venv .build_venv || goto :fail
)
".build_venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".build_venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller || goto :fail

echo.
echo  [2/4] Packaging the program...
if exist dist\ShopDesk rmdir /s /q dist\ShopDesk
".build_venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --distpath dist --workpath build installer\shopdesk.spec || goto :fail
if not exist "dist\ShopDesk\ShopDesk.exe" goto :fail

echo.
echo  [3/4] Looking for Inno Setup...
set "ISCC="
for %%P in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe") do (
  if exist %%P set "ISCC=%%~P"
)
if not defined ISCC (
  echo  Inno Setup not found. Trying to install it with winget...
  winget install -e --id JRSoftware.InnoSetup --silent --accept-package-agreements --accept-source-agreements >nul 2>nul
  for %%P in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe") do (
    if exist %%P set "ISCC=%%~P"
  )
)
if not defined ISCC (
  echo.
  echo  Inno Setup is not installed. Download the free "Inno Setup 6" from https://jrsoftware.org/isdl.php
  echo  install it, then run this file again.
  echo  The program itself is already packaged in dist\ShopDesk - you can run ShopDesk.exe from there.
  goto :fail
)

echo.
echo  [4/4] Building the installer...
"%ISCC%" installer\shopdesk.iss || goto :fail

echo.
echo  ===== Done =====
echo  Your installer: installer_output\ShopDesk-Setup-1.0.0.exe
echo.
explorer installer_output
pause
exit /b 0

:fail
echo.
echo  The build did not finish. Read the message above, fix it, and run this file again.
pause
exit /b 1
