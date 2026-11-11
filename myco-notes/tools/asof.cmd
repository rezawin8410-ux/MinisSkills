@echo off
REM asof: Windows launcher for asof.py
where python >nul 2>nul
if %errorlevel%==0 (python -B "%~dp0asof.py" %*) else (py -3 -B "%~dp0asof.py" %*)
exit /b %errorlevel%
