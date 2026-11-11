@echo off
REM notes-commit: Windows launcher for notes_commit.py
where python >nul 2>nul
if %errorlevel%==0 (python -B "%~dp0notes_commit.py" %*) else (py -3 -B "%~dp0notes_commit.py" %*)
exit /b %errorlevel%
