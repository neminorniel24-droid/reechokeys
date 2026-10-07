@echo off
echo Installing requirements...
python -m pip install -r requirements.txt pyinstaller
echo Building KeySounds.exe ...
python -m PyInstaller --onefile --noconsole --name KeySounds keysounds.py
echo.
echo Done! Your app is at: dist\KeySounds.exe
pause
