@echo off
REM relocate: Windows launcher for relocate.py
where python >nul 2>nul
if %errorlevel%==0 (python -B "%~dp0relocate.py" %*) else (py -3 -B "%~dp0relocate.py" %*)
exit /b %errorlevel%
