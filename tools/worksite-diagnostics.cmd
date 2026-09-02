@echo off
rem Диагностика комплекта: ничего не устанавливает, только читает состояние
rem и сохраняет отчёт в папку «Ответ с рабочего ПК» рядом с этим файлом.
setlocal
set "PS=powershell.exe"
where pwsh.exe >nul 2>&1 && set "PS=pwsh.exe"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0worksite-diagnostics.ps1" -BundleRoot "%~dp0."
echo.
pause
