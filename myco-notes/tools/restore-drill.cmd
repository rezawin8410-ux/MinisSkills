@echo off
REM restore-drill: Windows launcher for restore_drill.py
where python >nul 2>nul
if %errorlevel%==0 (python -B "%~dp0restore_drill.py" %*) else (py -3 -B "%~dp0restore_drill.py" %*)
exit /b %errorlevel%
