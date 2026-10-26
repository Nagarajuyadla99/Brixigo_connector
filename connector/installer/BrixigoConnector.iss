; Inno Setup script — Brixigo Connector Agent
; Build: ISCC.exe installer\BrixigoConnector.iss

#define MyAppName "Brixigo Connector Agent"
#define MyAppVersion "3.0.2"
#define MyAppPublisher "Brixigo"
#define MyAppURL "https://brixigo.com"
#define MyAppExeName "BrixigoConnector.exe"
#define MyServiceExeName "BrixigoConnectorService.exe"

[Setup]
AppId={{8F3C2A91-BRIX-4E10-IGO1-CONNECTOR0001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\Brixigo\Connector
DefaultGroupName=Brixigo Connector
DisableProgramGroupPage=no
OutputDir=..\dist\installer
OutputBaseFilename=BrixigoConnectorSetup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
Name: "autostart"; Description: "Start connector tray with Windows"; GroupDescription: "Startup:"

; Runtime data/logs are created by the app/service and are NOT listed in [Files],
; so upgrades replace binaries only and preserve settings, devices, queue, and logs.
[Dirs]
Name: "{app}\data"; Permissions: users-modify
Name: "{app}\logs"; Permissions: users-modify

[Files]
; onedir builds — copy full dependency trees (single process each; no onefile bootloader child)
Source: "..\dist\BrixigoConnector\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\BrixigoConnectorService\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\installer\setup_service.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\installer\uninstall_service.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\View Logs"; Filename: "{app}\logs"; Flags: foldershortcut
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Autostart stays tray-only; interactive Start Menu / desktop launches open the dashboard.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "BrixigoConnector"; ValueData: """{app}\{#MyAppExeName}"" --minimized"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\setup_service.ps1"" -InstallDir ""{app}"""; StatusMsg: "Registering Windows Service..."; Flags: runhidden waituntilterminated
; Tray launches UI-only when the service already owns the engine (no dual sync).
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Brixigo Connector"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\uninstall_service.ps1"""; RunOnceId: "UninstallSvc"; Flags: runhidden waituntilterminated
