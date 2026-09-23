@echo off
cd /d "%~dp0"
set /p URL=Paste your YouTube VOD URL:
python lavri_bot_v2.py --video "%URL%"
pause
