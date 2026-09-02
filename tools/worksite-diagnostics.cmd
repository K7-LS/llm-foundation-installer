@echo off
rem Diagnostics launcher: reads state only, writes a JSON report next to itself.
rem Kept ASCII on purpose: cmd.exe reads .cmd in the console code page.
rem Shell is located by explicit path: PATH may be restricted by policy.
setlocal
set "SCRIPT=%~dp0worksite-diagnostics.ps1"
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" set "PS=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not exist "%PS%" (
  echo PowerShell not found. Run worksite-diagnostics.ps1 manually.
  pause
  exit /b 1
)
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -BundleRoot "%~dp0."
echo.
pause
