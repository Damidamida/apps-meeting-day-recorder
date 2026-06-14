#define MyAppName "BK Scribe"
#define MyAppExeName "BK Scribe.exe"
#define MyAppPublisher "BK"
#define MyAppVersion "0.1.0"

[Setup]
AppId=BK.BKScribe
AppName=BK Scribe
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\BK Scribe
DefaultGroupName=BK Scribe
DisableDirPage=no
DisableProgramGroupPage=no
OutputDir=..\output
OutputBaseFilename=BK-Scribe-Setup
SetupIconFile=..\..\app\assets\bk_scribe.ico
UninstallDisplayIcon={app}\BK Scribe.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64
CloseApplications=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Messages]
WelcomeLabel1=Установка BK Scribe
WelcomeLabel2=Мастер установит BK Scribe только для текущего пользователя.
SelectDirDesc=Выберите папку, куда будут установлены файлы приложения. Рабочие данные хранятся отдельно и не удаляются при обновлении.

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"; Flags: checkedonce

[Files]
Source: "..\..\dist\BK Scribe\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\BK Scribe"; Filename: "{app}\BK Scribe.exe"; WorkingDir: "{app}"; IconFilename: "{app}\BK Scribe.exe"
Name: "{autodesktop}\BK Scribe"; Filename: "{app}\BK Scribe.exe"; WorkingDir: "{app}"; IconFilename: "{app}\BK Scribe.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\BK Scribe.exe"; Description: "Запустить BK Scribe"; Flags: nowait postinstall skipifsilent
