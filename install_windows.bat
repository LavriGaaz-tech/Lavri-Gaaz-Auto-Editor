@echo off
cd /d "%~dp0"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo Make sure FFmpeg is installed and available as "ffmpeg" and "ffprobe".
echo Edit config.json with your YouTube channel URL.
pause
