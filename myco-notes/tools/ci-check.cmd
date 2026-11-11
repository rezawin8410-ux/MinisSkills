@echo off
REM ci-check: Windows launcher for ci_check.py
where python >nul 2>nul
if %errorlevel%==0 (python -B "%~dp0ci_check.py" %*) else (py -3 -B "%~dp0ci_check.py" %*)
exit /b %errorlevel%
