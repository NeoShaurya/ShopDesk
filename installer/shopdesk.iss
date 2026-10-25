; Inno Setup script for ShopDesk (compiled by BUILD_INSTALLER.bat)
#define AppName "ShopDesk"
#define AppVersion "1.0.0"

[Setup]
AppId={{B6E1C2A4-5D3F-4C1E-9A77-2F4D8E0A9C11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=ShopDesk
DefaultDirName={autopf}\ShopDesk
DefaultGroupName=ShopDesk
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64
OutputDir=..\installer_output
OutputBaseFilename=ShopDesk-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\ShopDesk.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\ShopDesk\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ShopDesk"; Filename: "{app}\ShopDesk.exe"; IconFilename: "{app}\ShopDesk.exe"
Name: "{autodesktop}\ShopDesk"; Filename: "{app}\ShopDesk.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ShopDesk.exe"; Description: "Start ShopDesk now"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    MsgBox('ShopDesk has been removed. Your shop data (bills, stock, customers) was NOT deleted. It stays in:' + #13#10 +
           ExpandConstant('{localappdata}\ShopDesk\data'), mbInformation, MB_OK);
end;
