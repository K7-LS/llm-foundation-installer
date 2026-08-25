@echo off
setlocal
set "K7_EXE="
for %%F in ("%~dp0*.exe") do if not defined K7_EXE set "K7_EXE=%%~fF"
if not defined K7_EXE exit /b 2
start "" "%K7_EXE%" --launch-center-ui
