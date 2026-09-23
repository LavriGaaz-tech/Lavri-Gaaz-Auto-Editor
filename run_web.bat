@echo off
cd /d "%~dp0"
python -m pip install -r requirements_web.txt
python web_app.py
pause
