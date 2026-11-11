@echo off
REM doctor: Windows launcher for doctor.py
where python >nul 2>nul
if %errorlevel%==0 (python -B "%~dp0doctor.py" %*) else (py -3 -B "%~dp0doctor.py" %*)
exit /b %errorlevel%
