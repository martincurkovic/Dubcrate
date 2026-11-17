; Inno Setup script for the Dubcrate Windows installer (https://jrsoftware.org/isinfo.php).
; scripts/build_windows.ps1 runs it (ISCC.exe) after the app is built; the version comes in with /DMyAppVersion=...
; AppId is this program's identity for Windows (upgrades and the uninstaller): generated once, never change it.

#define MyAppName "Dubcrate"
#define MyAppPublisher "Martin Curkovic"
#define MyAppURL "https://github.com/martincurkovic/Dubcrate"
#define MyAppExeName "main.exe"

[Setup]
AppId={{55245456-2CDF-42A4-8542-3461EEB69FCA}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Qt 6.12 needs Windows 10 (1809) or newer
MinVersion=10.0.17763
; installs for the current user (no administrator prompt) unless the user chooses otherwise
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputBaseFilename=Dubcrate-windows-x64-Setup
OutputDir=..\..
Compression=lzma
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\..\LICENSE
SetupIconFile=..\icon\Dubcrate.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "..\..\src\Dubcrate.dist\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
