@echo off
title MM Bot Dashboard Launcher
echo Checking dependencies...
python -m pip install -r requirements.txt --quiet
echo.
echo Starting Market Maker Dashboard...
echo Dashboard will open at http://localhost:8000
echo.
python main.py
pause
