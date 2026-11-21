@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Video to Scratch
color 0F

echo.
echo   ==========================================
echo     Video to Scratch  -  first run setup
echo   ==========================================
echo.

set "VENV=%~dp0.venv"
set "VPY=%VENV%\Scripts\python.exe"
set "PYEXE="

rem ---------- 1. Python + virtual environment ----------
if exist "%VPY%" goto have_venv

call :find_python
if defined PYEXE goto have_python

echo [1/4] Python was not found. Downloading Python 3.12 - about 25 MB ...
set "PYINST=%TEMP%\v2s_python_installer.exe"
call :download "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe" "%PYINST%"
if not exist "%PYINST%" goto fail_python
echo       Installing Python, please wait ...
"%PYINST%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=0 Include_test=0 Shortcuts=0
del "%PYINST%" >nul 2>nul
call :find_python
if not defined PYEXE goto fail_python

:have_python
echo [1/4] Creating environment ...
%PYEXE% -m venv "%VENV%"
if not exist "%VPY%" goto fail_venv
goto have_venv

:fail_python
echo.
echo   Could not install Python automatically.
echo   Install Python 3.12 from https://www.python.org/downloads/
echo   Tick "Add python.exe to PATH", then run start.bat again.
pause
exit /b 1

:fail_venv
echo.
echo   Could not create the virtual environment.
pause
exit /b 1

:have_venv
echo [1/4] Python ............ ok

rem ---------- 2. pywebview ----------
if exist "%VENV%\.deps_ok" goto deps_ok
echo [2/4] Installing pywebview ...
"%VPY%" -m pip install --disable-pip-version-check -q pywebview
if errorlevel 1 goto fail_deps
echo ok> "%VENV%\.deps_ok"
:deps_ok
echo [2/4] pywebview ......... ok

rem ---------- 3. WebView2 runtime ----------
set "WV2=0"
reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" /v pv >nul 2>nul && set "WV2=1"
reg query "HKLM\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" /v pv >nul 2>nul && set "WV2=1"
reg query "HKCU\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" /v pv >nul 2>nul && set "WV2=1"
if "%WV2%"=="1" goto wv2_ok
echo [3/4] Installing Microsoft WebView2 runtime ...
set "WVINST=%TEMP%\v2s_webview2.exe"
call :download "https://go.microsoft.com/fwlink/p/?LinkId=2124703" "%WVINST%"
if exist "%WVINST%" "%WVINST%" /silent /install
del "%WVINST%" >nul 2>nul
:wv2_ok
echo [3/4] WebView2 ......... ok

rem ---------- 4. ffmpeg ----------
if exist "%~dp0bin\ffmpeg.exe" goto ff_ok
echo [4/4] Downloading ffmpeg - about 80 MB, one time only ...
if not exist "%~dp0bin" mkdir "%~dp0bin"
set "FFZIP=%TEMP%\v2s_ffmpeg.zip"
set "FFTMP=%TEMP%\v2s_ffmpeg"
call :download "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" "%FFZIP%"
if not exist "%FFZIP%" call :download "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" "%FFZIP%"
if not exist "%FFZIP%" goto fail_ff
echo       Unpacking ...
if exist "%FFTMP%" rmdir /s /q "%FFTMP%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%FFZIP%' -DestinationPath '%FFTMP%' -Force"
for /r "%FFTMP%" %%F in (ffmpeg.exe) do if exist "%%F" copy /y "%%F" "%~dp0bin\ffmpeg.exe" >nul
if exist "%FFTMP%" rmdir /s /q "%FFTMP%"
del "%FFZIP%" >nul 2>nul
if not exist "%~dp0bin\ffmpeg.exe" goto fail_ff
:ff_ok
echo [4/4] ffmpeg ............ ok

rem ---------- launch ----------
"%VPY%" -c "import webview" >nul 2>nul
if errorlevel 1 goto fail_deps
echo.
echo   Starting ...
start "" "%VENV%\Scripts\pythonw.exe" "%~dp0app.py"
exit /b 0

:fail_deps
if exist "%VENV%\.deps_ok" del "%VENV%\.deps_ok"
echo.
echo   Could not install pywebview. Check your internet connection
echo   and run start.bat again.
pause
exit /b 1

:fail_ff
echo.
echo   Could not download ffmpeg. Check your internet connection
echo   and run start.bat again. Or put ffmpeg.exe into the "bin" folder by hand.
pause
exit /b 1


rem ================= helpers =================

:find_python
set "PYEXE="
for %%C in ("py -3.12" "py -3.13" "py -3.11" "py -3.10" "py -3.9" "python" "python3") do (
  if not defined PYEXE (
    %%~C -c "import sys; sys.exit(0 if (3,9)<=sys.version_info[:2]<=(3,13) else 1)" >nul 2>nul && set "PYEXE=%%~C"
  )
)
if defined PYEXE exit /b 0
for %%P in ("%LocalAppData%\Programs\Python\Python312\python.exe" "%LocalAppData%\Programs\Python\Python313\python.exe" "%LocalAppData%\Programs\Python\Python311\python.exe") do (
  if not defined PYEXE if exist %%P set PYEXE=%%P
)
exit /b 0

:download
rem %~1 = url, %~2 = target file
if exist "%~2" del "%~2" >nul 2>nul
where curl >nul 2>nul
if errorlevel 1 goto download_ps
curl -L --fail --progress-bar -o "%~2" "%~1"
if errorlevel 1 if exist "%~2" del "%~2" >nul 2>nul
exit /b 0
:download_ps
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -UseBasicParsing -Uri '%~1' -OutFile '%~2' } catch { exit 1 }"
if errorlevel 1 if exist "%~2" del "%~2" >nul 2>nul
exit /b 0
