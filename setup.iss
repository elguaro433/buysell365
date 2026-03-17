; ============================================
; BuySell365 Pro — Inno Setup Installer Script
; ============================================
; Download Inno Setup from: https://jrsoftware.org/isinfo.php
; Open this file with Inno Setup Compiler and click Build.

[Setup]
AppId={{B5365-PRO-2026-TRADING-AI}}
AppName=BuySell365 Pro
AppVersion=1.0.0
AppVerName=BuySell365 Pro 1.0
AppPublisher=BuySell365
AppPublisherURL=https://buysell365.pro
AppSupportURL=https://buysell365.pro
DefaultDirName={autopf}\BuySell365 Pro
DefaultGroupName=BuySell365 Pro
AllowNoIcons=yes
; Output installer location
OutputDir=installer_output
OutputBaseFilename=BuySell365_Pro_Setup_v1.0
; Icon
SetupIconFile=static\bull-logo.ico
UninstallDisplayIcon={app}\BuySell365 Pro.exe
; Compression
Compression=lzma2/ultra64
SolidCompression=yes
; Privileges (user install, no admin required)
PrivilegesRequired=lowest
; UI
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el Escritorio"; GroupDescription: "Iconos adicionales:"
Name: "autostart"; Description: "Iniciar automaticamente con Windows"; GroupDescription: "Inicio automatico:"

[Files]
; Copy the entire PyInstaller dist folder
Source: "dist\BuySell365 Pro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start menu
Name: "{group}\BuySell365 Pro"; Filename: "{app}\BuySell365 Pro.exe"
Name: "{group}\Desinstalar BuySell365 Pro"; Filename: "{uninstallexe}"
; Desktop shortcut
Name: "{autodesktop}\BuySell365 Pro"; Filename: "{app}\BuySell365 Pro.exe"; Tasks: desktopicon
; Auto-start with Windows
Name: "{userstartup}\BuySell365 Pro"; Filename: "{app}\BuySell365 Pro.exe"; Tasks: autostart

[Run]
; Launch after install
Filename: "{app}\BuySell365 Pro.exe"; Description: "Iniciar BuySell365 Pro"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: files; Name: "{app}\launcher_config.json"

[Code]
// Show a custom welcome message
procedure InitializeWizard;
begin
  WizardForm.WelcomeLabel2.Caption :=
    'Este asistente instalara BuySell365 Pro en su equipo.' + #13#10 + #13#10 +
    'BuySell365 Pro es un sistema de trading algoritmico con ' +
    'Inteligencia Artificial, analisis tecnico profesional y ' +
    'ejecucion automatica en MetaTrader 5.' + #13#10 + #13#10 +
    'Se recomienda cerrar todas las aplicaciones antes de continuar.';
end;
