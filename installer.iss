; Inno Setup script - makes ReechoKeys-Setup.exe (a normal Windows installer)
[Setup]
AppName=ReechoKeys
AppVersion=1.2
AppPublisher=ReechoKeys
DefaultDirName={autopf}\ReechoKeys
DefaultGroupName=ReechoKeys
OutputDir=installer
OutputBaseFilename=ReechoKeys-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\ReechoKeys.exe
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"
Name: "startup"; Description: "Start ReechoKeys automatically when Windows starts"

[Files]
Source: "dist\ReechoKeys\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ReechoKeys"; Filename: "{app}\ReechoKeys.exe"
Name: "{autodesktop}\ReechoKeys"; Filename: "{app}\ReechoKeys.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ReechoKeys"; ValueData: """{app}\ReechoKeys.exe"" --minimized"; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\ReechoKeys.exe"; Description: "Start ReechoKeys now"; Flags: nowait postinstall skipifsilent
