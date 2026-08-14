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
!ifndef LICENSE_FILE
  !error "LICENSE_FILE is required"
!endif
!ifndef OUTPUT_FILE
  !error "OUTPUT_FILE is required"
!endif
!ifndef APP_ICON
  !error "APP_ICON is required"
!endif

Name "Power Test Full Setup"
OutFile "${OUTPUT_FILE}"
Icon "${APP_ICON}"
InstallDir "$PROGRAMFILES64\Power Test"
InstallDirRegKey HKLM "Software\ARP\Power Test" "InstallDir"
BrandingText "Power Test Offline Driver Bundle"
ShowInstDetails show
SetCompressor /SOLID lzma
SetCompressorDictSize 32

VIProductVersion "0.1.0.0"
VIAddVersionKey /LANG=2052 "ProductName" "Power Test Full Setup"
VIAddVersionKey /LANG=2052 "CompanyName" "ARP"
VIAddVersionKey /LANG=2052 "LegalCopyright" "Copyright (C) ARP"
VIAddVersionKey /LANG=2052 "FileDescription" "Power Test 与硬件驱动离线完整安装包"
VIAddVersionKey /LANG=2052 "FileVersion" "0.1.0"
VIAddVersionKey /LANG=2052 "ProductVersion" "0.1.0"

!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_NOAUTOCLOSE
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${LICENSE_FILE}"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP|MB_OK "Power Test 完整安装包仅支持 64 位 Windows。"
    Abort
  ${EndIf}
  SetRegView 64
FunctionEnd

Section "Power Test 与全部驱动" SEC_MAIN
  SetDetailsPrint both
  SetOutPath "$PLUGINSDIR\payload"
  File /oname=PowerTest.msi "${APP_MSI}"
  File /oname=install_full_bundle.ps1 "${INSTALL_SCRIPT}"

  SetOutPath "$PLUGINSDIR\payload\drivers"
  File /r "${DRIVER_ROOT}\*.*"

  DetailPrint "正在安装硬件驱动、NI 运行环境和 Power Test..."
  nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\payload\install_full_bundle.ps1" -PayloadRoot "$PLUGINSDIR\payload\drivers" -AppMsiPath "$PLUGINSDIR\payload\PowerTest.msi" -AppInstallDir "$INSTDIR"'
  Pop $0

  ${If} $0 == "3010"
    WriteRegStr HKLM "Software\ARP\Power Test" "InstallDir" "$INSTDIR"
    SetRebootFlag true
    DetailPrint "安装成功，需要重启 Windows。"
  ${ElseIf} $0 != "0"
    MessageBox MB_ICONSTOP|MB_OK "完整安装失败（错误码 $0）。日志位于 C:\ProgramData\Power Test\InstallerLogs。"
    Abort
  ${Else}
    WriteRegStr HKLM "Software\ARP\Power Test" "InstallDir" "$INSTDIR"
    DetailPrint "Power Test 和全部驱动安装成功。"
  ${EndIf}
SectionEnd
