; Build: python build_exe.py && iscc installer\Norilan.iss  ->  dist\Norilan-Setup.exe
#define AppName "Norilan"
#define AppVersion "0.5.0"
#define AppExe "Norilan.exe"

[Setup]
AppId={{B6D1A0C4-7E55-4C3B-9B7B-2D4B6F1E0A31}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Norilan-Setup
SetupIconFile=..\build\norilan.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; Đóng Norilan đang chạy (kể cả tiến trình nền ở khay) trước khi ghi đè; mở lại sau khi cài xong.
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Tạo biểu tượng trên màn hình nền"; Flags: unchecked
Name: "autostart"; Description: "Chạy nền cùng Windows (để nhận lời nhắc khi đã đóng cửa sổ)"

[Files]
Source: "..\dist\Norilan\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Gỡ cài đặt {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Chính exe này với --background. App tự đồng bộ lại theo cài đặt của người dùng mỗi lần mở.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; \
  ValueData: """{app}\{#AppExe}"" --background"; Tasks: autostart; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\norilan"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\Norilan.Desktop"; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "Mở {#AppName}"; Flags: nowait postinstall skipifsilent
; Cài/nâng cấp xong: bật lại tiến trình nền (single-instance nên không bao giờ chạy hai bản).
Filename: "{app}\{#AppExe}"; Parameters: "--background"; Flags: nowait runhidden; Tasks: autostart; Check: WizardSilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "KillNorilan"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  Dir, Path: String;
begin
  { Cài mới + chọn chạy nền: ghi autostart=true (app đồng bộ khóa Run theo settings mỗi lần mở).
    Nâng cấp (đã có settings.json) thì giữ nguyên lựa chọn của người dùng. }
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('autostart') then
  begin
    Dir := ExpandConstant('{userappdata}\Norilan');
    Path := Dir + '\settings.json';
    if not FileExists(Path) then
    begin
      ForceDirectories(Dir);
      SaveStringToFile(Path, '{"autostart": true}', False);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { Mặc định GIỮ dữ liệu; chỉ xóa khi người dùng chọn rõ ràng. }
  if CurUninstallStep = usPostUninstall then
    if not UninstallSilent then
      if MsgBox('Xóa luôn dữ liệu của bạn (lịch, bài tập, file đính kèm, ảnh nền, sao lưu)?' + #13#10 +
                'Chọn "Không" để giữ lại.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(ExpandConstant('{userappdata}\Norilan'), True, True, True);
end;
