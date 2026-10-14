; Установщик AE Subs (Inno Setup 6.5+). Собирается командой: python tools\make_installer.py
; Программа входит в установщик, а модель распознавания, ускорение на видеокарте и отделение голоса
; по выбору скачиваются с Hugging Face во время установки (downloads.iss генерирует make_installer.py).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8C7A3F2E-5B1D-4E9A-9F3C-AE5B0B5D1A27}
AppName=AE Subs
AppVersion={#AppVersion}
AppVerName=AE Subs {#AppVersion}
AppPublisher=Yaqen
AppPublisherURL=https://github.com/Lanceload/AE-Subs
AppSupportURL=https://github.com/Lanceload/AE-Subs
AppUpdatesURL=https://github.com/Lanceload/AE-Subs/releases
VersionInfoVersion={#AppVersion}
; установка для текущего пользователя: программа сама обновляется, ей нужна папка, куда можно писать
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\AE Subs
UsePreviousAppDir=yes
DisableProgramGroupPage=yes
OutputDir=..\release
OutputBaseFilename=AE_Subs_Setup_{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\AE Subs.exe
UninstallDisplayName=AE Subs
WizardStyle=modern
WizardSmallImageFile=wizard_small.bmp,wizard_small_2x.bmp
WizardImageFile=wizard.bmp,wizard_2x.bmp
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Messages]
SelectDirBrowseLabel=Чтобы продолжить, нажмите «Далее».%n%nМодели распознавания хранятся рядом с программой и занимают несколько гигабайт — выберите диск, где достаточно места.

[Types]
Name: "full"; Description: "Рекомендуемая установка"
Name: "custom"; Description: "Выборочная установка"; Flags: iscustom

[Components]
Name: "app"; Description: "AE Subs {#AppVersion}"; Types: full custom; Flags: fixed
Name: "model"; Description: "Модель распознавания речи Turbo (иначе скачается при первом распознавании)"; Types: full
Name: "gpu"; Description: "Ускорение распознавания на видеокарте NVIDIA (CUDA)"; Types: full
Name: "vocals"; Description: "Отделение голоса от музыки"; Types: full

[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; GroupDescription: "Ярлыки:"

[Files]
Source: "..\build\out\AE Subs\*"; DestDir: "{app}"; Components: app; Flags: ignoreversion recursesubdirs createallsubdirs
#include "downloads.iss"

[Icons]
Name: "{autoprograms}\AE Subs"; Filename: "{app}\AE Subs.exe"
Name: "{autodesktop}\AE Subs"; Filename: "{app}\AE Subs.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AE Subs.exe"; Description: "Запустить AE Subs"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; файлы, которые программа докачала или обновила сама; пресеты и проекты пользователя остаются
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\models"
Type: filesandordirs; Name: "{app}\cuda"

[Code]
var
  GpuChecked: Boolean;

function HasNvidia: Boolean;
begin
  { драйвер NVIDIA кладёт nvcuda.dll в System32 }
  Result := FileExists(ExpandConstant('{sys}\nvcuda.dll'));
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { без видеокарты NVIDIA ускорение бесполезно — по умолчанию не качаем 2 ГБ }
  if (CurPageID = wpSelectComponents) and not GpuChecked then
  begin
    GpuChecked := True;
    if not HasNvidia then
      WizardSelectComponents('!gpu');
  end;
end;
