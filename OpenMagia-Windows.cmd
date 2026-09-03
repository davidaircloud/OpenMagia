@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0OpenMagia-Windows.ps1"
if errorlevel 1 pause
endlocal
