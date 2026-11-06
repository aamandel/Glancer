; Glancer installer (Inno Setup 6). Built by build.ps1:
;   python -m glancer.win.associations --inno > installer\associations.iss
;   iscc installer\glancer.iss            -> dist\GlancerSetup.exe
;
; Installs dist\Glancer\ to Program Files, registers Glancer for its file types
; in HKLM (the [Registry] lines come from associations.iss, generated from the
; same list the portable build uses), and adds a Start menu shortcut. The
; uninstaller removes the files and exactly those registry entries.

#define AppName "Glancer"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6C1C5E7B-3A1F-4F57-9E0E-4C9F2B9D6A11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Aaron Mandel
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=GlancerSetup
SetupIconFile=..\assets\glancer.ico
UninstallDisplayIcon={app}\Glancer.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Tells Explorer the file associations changed (SHChangeNotify) after install/uninstall.
ChangesAssociations=yes

[Files]
Source: "..\dist\Glancer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\Glancer.exe"

#include "associations.iss"

[Run]
; Windows 11 doesn't let an app make itself the default: open Default apps so the user can.
Filename: "ms-settings:defaultapps?registeredAppUser=Glancer"; Description: "Choose Glancer as the default app in Settings"; Flags: postinstall shellexec skipifsilent unchecked
Filename: "{app}\Glancer.exe"; Description: "Start Glancer"; Flags: postinstall nowait skipifsilent unchecked
