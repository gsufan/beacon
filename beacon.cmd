@echo off
if "%~1"=="" goto shell
"%~dp0.venv\Scripts\beacon.exe" %*
goto :eof
:shell
cmd /k "%~dp0.venv\Scripts\activate.bat"
