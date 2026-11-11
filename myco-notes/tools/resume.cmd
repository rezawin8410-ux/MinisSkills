@echo off
REM resume: Windows launcher for resume.py
where python >nul 2>nul
if %errorlevel%==0 (python -B "%~dp0resume.py" %*) else (py -3 -B "%~dp0resume.py" %*)
exit /b %errorlevel%
