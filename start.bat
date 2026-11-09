@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py -3 server.py & goto :end)
where python >nul 2>nul && (python server.py & goto :end)
echo.
echo Python is not installed. Download it from https://www.python.org/downloads/
echo During install, tick "Add Python to PATH", then double-click start.bat again.
:end
pause
