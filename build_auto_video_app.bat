@echo off
echo Building auto_video_app_voicevox.exe...

REM Xóa các thư mục build cũ (nếu có)
rmdir /s /q build
rmdir /s /q dist
del auto_video_app_voicevox.spec

REM Tiến hành build với PyInstaller
pyinstaller --onefile --noconsole ^
--add-data "voicevox_speakers.json;." ^
--add-data "effects;effects" ^
--add-data "ffmpeg;ffmpeg" ^
auto_video_app_voicevox.py

echo Done! EXE created in the 'dist' folder.
pause
