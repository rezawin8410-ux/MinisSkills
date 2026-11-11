@echo off
REM setup-clone: Windows launcher for setup_clone.py
where python >nul 2>nul
if %errorlevel%==0 (python -B "%~dp0setup_clone.py" %*) else (py -3 -B "%~dp0setup_clone.py" %*)
exit /b %errorlevel%
