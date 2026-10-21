; ClipForge-Installer (Inno Setup 6). Wird von installer\build.ps1 aufgerufen.
; Installiert ohne Adminrechte nach %LOCALAPPDATA%\Programs\ClipForge.

#ifndef MyVersion
  #define MyVersion "0.4.2"
#endif
#ifndef Stage
  #define Stage "..\build\stage"
#endif
#ifndef OutDir
  #define OutDir "..\dist"
#endif

[Setup]
AppId={{6F1E4B3A-9C2D-4E8F-A1B7-C1F0D2E3A4B5}
AppName=ClipForge
AppVersion={#MyVersion}
AppVerName=ClipForge {#MyVersion}
AppPublisher=Morni
AppPublisherURL=https://github.com/MoinMornhart/ClipForge
AppSupportURL=https://github.com/MoinMornhart/ClipForge/issues
DefaultDirName={localappdata}\Programs\ClipForge
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir={#OutDir}
OutputBaseFilename=ClipForge-Setup-{#MyVersion}
SetupIconFile={#Stage}\clipforge\ui\assets\icon.ico
UninstallDisplayIcon={app}\ClipForge.exe
UninstallDisplayName=ClipForge
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[Messages]
de.WelcomeLabel2=ClipForge findet die besten Momente in deinen Twitch- und Kick-VODs und schneidet sie fertig für TikTok, YouTube Shorts und Instagram Reels.%n%nBei der Installation werden einmalig ca. 3 GB an Bibliotheken aus dem Internet geladen.

[Tasks]
Name: "desktopicon"; Description: "Desktop-Verknüpfung anlegen"; GroupDescription: "Verknüpfungen:"

[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\ClipForge"; Filename: "{app}\ClipForge.exe"; Comment: "VODs zu Kurzclips"
Name: "{autodesktop}\ClipForge"; Filename: "{app}\ClipForge.exe"; Tasks: desktopicon; Comment: "VODs zu Kurzclips"

[Run]
Filename: "{cmd}"; Parameters: "/c ""{app}\setup_runtime.bat"""; WorkingDir: "{app}"; StatusMsg: "Lade Bibliotheken (einmalig ca. 3 GB) – das Konsolenfenster zeigt den Fortschritt …"; Flags: waituntilterminated
Filename: "{app}\ClipForge.exe"; Description: "ClipForge jetzt starten"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; per pip nachinstallierte Bibliotheken und Updates gehören nicht zum Installer-Paket
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\clipforge"
