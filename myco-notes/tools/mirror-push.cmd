@echo off
REM mirror-push: Windows launcher for mirror_push.py
where python >nul 2>nul
if %errorlevel%==0 (python -B "%~dp0mirror_push.py" %*) else (py -3 -B "%~dp0mirror_push.py" %*)
exit /b %errorlevel%
