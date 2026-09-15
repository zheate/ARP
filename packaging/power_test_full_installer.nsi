Unicode true
RequestExecutionLevel admin
ManifestDPIAware true
!include "MUI2.nsh"
!include "x64.nsh"
!include "LogicLib.nsh"
!ifndef APP_MSI
  !error "APP_MSI is required"
!endif
!ifndef DRIVER_ROOT
  !error "DRIVER_ROOT is required"
!endif
!ifndef INSTALL_SCRIPT
  !error "INSTALL_SCRIPT is required"
!endif
!ifndef OFFLINE_MODULES
  !error "OFFLINE_MODULES is required"
!endif
!ifndef MANIFEST_HASH
  !error "MANIFEST_HASH is required"
!endif
!ifndef LICENSE_FILE
  !error "LICENSE_FILE is required"
!endif
!ifndef OUTPUT_FILE
  !error "OUTPUT_FILE is required"
!endif
!ifndef APP_ICON
  !error "APP_ICON is required"
!endif
!ifndef APP_VERSION
  !error "APP_VERSION is required"
!endif
Name "Power Test Offline Setup"
OutFile "${OUTPUT_FILE}"
Icon "${APP_ICON}"
InstallDir "$PROGRAMFILES64\Power Test"
InstallDirRegKey HKLM "Software\ARP\Power Test" "InstallDir"
BrandingText "Power Test Offline Deployment"
ShowInstDetails show
SetCompressor /SOLID lzma
SetCompressorDictSize 32
VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=2052 "ProductName" "Power Test Offline Setup"
VIAddVersionKey /LANG=2052 "CompanyName" "ARP"
VIAddVersionKey /LANG=2052 "LegalCopyright" "Copyright (C) ARP"
VIAddVersionKey /LANG=2052 "FileDescription" "Power Test and offline runtime deployment"
VIAddVersionKey /LANG=2052 "FileVersion" "${APP_VERSION}"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_NOAUTOCLOSE
!define MUI_WELCOMEPAGE_TEXT "本安装包将自动安装 Power Test、运行组件和支持的设备驱动，不需要联网。$\r$\n$\r$\n请先安全结束测试、关闭 Power Test，并断开测试仪器。下一步可选择应用安装目录。系统驱动仍安装在 Windows 标准位置。"
!define MUI_FINISHPAGE_TEXT "Power Test 安装与组件检查已完成。$\r$\n$\r$\n需要重启时，请完成重启后再连接仪器。首次使用仍需按既有离线流程导入许可证。"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${LICENSE_FILE}"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"
Var NativePowerShell
Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP|MB_OK "Power Test requires 64-bit Windows."
    SetErrorLevel 1
    Abort
  ${EndIf}
  SetRegView 64
  SetShellVarContext all
  ; NSIS is 32-bit. Sysnative avoids WOW64 redirection for COM/Driver Store.
  StrCpy $NativePowerShell "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
  IfFileExists "$NativePowerShell" +4 0
    MessageBox MB_ICONSTOP|MB_OK "Native Windows PowerShell is unavailable."
    SetErrorLevel 1
    Abort
FunctionEnd
Section "Power Test offline deployment" SEC_MAIN
  InitPluginsDir
  SetOutPath "$PLUGINSDIR\payload"
  File /oname=PowerTest.msi "${APP_MSI}"
  File /oname=install_full_bundle.ps1 "${INSTALL_SCRIPT}"
  SetOutPath "$PLUGINSDIR\payload\offline"
  File "${OFFLINE_MODULES}\Common.ps1"
  File "${OFFLINE_MODULES}\Payload.ps1"
  File "${OFFLINE_MODULES}\Components.ps1"
  File "${OFFLINE_MODULES}\Application.ps1"
  SetOutPath "$PLUGINSDIR\payload\drivers"
  File /r "${DRIVER_ROOT}\*.*"
  DetailPrint "正在校验离线组件并安装运行环境、驱动和 Power Test..."
  ; Pass the operator's path as data, not interpolated command-line switches.
  System::Call 'kernel32::SetEnvironmentVariableW(w "ARP_SETUP_INSTALLDIR", w "$INSTDIR") i .r1'
  ${If} $1 == 0
    SetErrorLevel 1
    Abort "Cannot transfer the selected application directory."
  ${EndIf}
  nsExec::ExecToLog '"$NativePowerShell" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\payload\install_full_bundle.ps1" -PayloadRoot "$PLUGINSDIR\payload\drivers" -AppMsiPath "$PLUGINSDIR\payload\PowerTest.msi" -ExpectedManifestSha256 "${MANIFEST_HASH}"'
  Pop $0
  System::Call 'kernel32::SetEnvironmentVariableW(w "ARP_SETUP_INSTALLDIR", p 0)'
  ${If} $0 == "3010"
    SetRebootFlag true
    SetErrorLevel 3010
  ${ElseIf} $0 != "0"
    MessageBox MB_ICONSTOP|MB_OK "安装未完成（错误 $0）。请查看详细输出和 $APPDATA\Power Test\InstallerLogs。已安装的共享驱动不会被自动删除。"
    SetErrorLevel 1
    Abort
  ${Else}
    SetErrorLevel 0
  ${EndIf}
  ; Only publish a successful install location AFTER actual MSI path checks.
  WriteRegStr HKLM "Software\ARP\Power Test" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\ARP\Power Test" "Version" "${APP_VERSION}"
  DetailPrint "Power Test 已安装至 $INSTDIR。"
  ; No elevated automatic app launch. The operator starts it after any reboot.
SectionEnd
