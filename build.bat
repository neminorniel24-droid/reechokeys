@echo off
echo [1/3] Installing requirements...
python -m pip install -r requirements.txt pyinstaller
echo [2/3] Building ReechoKeys.exe ...
python -m PyInstaller --onefile --noconsole --icon icon.ico --add-data "icon.ico;." --collect-all customtkinter --name ReechoKeys reechokeys.py
echo [3/3] Building installer (needs Inno Setup, free from jrsoftware.org)...
set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist %ISCC% (
  %ISCC% installer.iss
  echo.
  echo DONE! Give people this file: installer\ReechoKeys-Setup.exe
) else (
  echo.
  echo Inno Setup not found. Your app is ready at dist\ReechoKeys.exe
  echo To also make a Setup installer, install Inno Setup and run build.bat again.
)
pause
