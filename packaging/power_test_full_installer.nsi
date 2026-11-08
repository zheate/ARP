Unicode true
RequestExecutionLevel admin
ManifestDPIAware true

!include "MUI2.nsh"
!include "x64.nsh"
!include "LogicLib.nsh"
!include "Sections.nsh"
!include "WinVer.nsh"

!ifndef APP_MSI
  !error "APP_MSI is required"
!endif
!ifndef DRIVER_ROOT
  !error "DRIVER_ROOT is required"
!endif
!ifndef INSTALL_SCRIPT
  !error "INSTALL_SCRIPT is required"
!endif
!ifndef HELPER_SCRIPT
  !error "HELPER_SCRIPT is required"
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

Name "Power Test 完整安装"
OutFile "${OUTPUT_FILE}"
Icon "${APP_ICON}"
InstallDir "$PROGRAMFILES64\Power Test"
InstallDirRegKey HKLM "Software\ARP\Power Test" "InstallDir"
BrandingText "Power Test · 软件、运行环境和设备驱动"
ShowInstDetails show
SetCompressor /SOLID lzma
SetCompressorDictSize 32

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=2052 "ProductName" "Power Test Full Setup"
VIAddVersionKey /LANG=2052 "CompanyName" "ARP"
VIAddVersionKey /LANG=2052 "LegalCopyright" "Copyright (C) ARP"
VIAddVersionKey /LANG=2052 "FileDescription" "Power Test application, WebView2 and device drivers"
VIAddVersionKey /LANG=2052 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${APP_VERSION}"

Var Components
Var SetupCache
Var FinishMessage
Var SetupMutex

!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_NOAUTOCLOSE
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${LICENSE_FILE}"
!define MUI_COMPONENTSPAGE_TEXT_TOP "默认安装完整设备支持。可取消不使用的设备；相关串口组件会自动勾选 NI-VISA。"
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TEXT "$FinishMessage"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

Function .onInit
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP|MB_OK "Power Test 需要 Windows 10 / 11。"
    Abort
  ${EndIf}
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP|MB_OK "Power Test requires 64-bit Windows."
    Abort
  ${EndIf}
  System::Call 'kernel32::CreateMutexW(p 0, i 0, w "Global\ARP-PowerTest-SetupUI") p .r0 ?e'
  Pop $1
  StrCpy $SetupMutex $0
  ${If} $1 == 183
    MessageBox MB_ICONSTOP|MB_OK "另一个 Power Test 安装程序正在运行。"
    Abort
  ${EndIf}
  System::Call 'kernel32::CreateMutexW(p 0, i 0, w "Global\ARP-PowerTest-FullSetup") p .r0 ?e'
  Pop $1
  System::Call 'kernel32::CloseHandle(p r0)'
  ${If} $1 == 183
    MessageBox MB_ICONSTOP|MB_OK "Power Test 正在继续安装。请等待安装结束。"
    Abort
  ${EndIf}
  StrCpy $Components ""
  StrCpy $FinishMessage "Power Test、WebView2 和所选驱动已安装。请启动软件完成激活。"
  SetRegView 64
  SetShellVarContext all
FunctionEnd

; All components are selected by default. Deselect devices that are not used.
Section "Power Test + WebView2（必需）" SEC_APP
  SectionIn RO
SectionEnd
Section "CH341 I²C 电源控制器" SEC_CH341
  StrCpy $Components "$Components,ch341"
SectionEnd
Section "Ocean Insight / OceanHR4 光谱仪" SEC_OCEAN
  StrCpy $Components "$Components,ocean"
SectionEnd
Section "LaserPoint / FTDI 功率计" SEC_FTDI
  StrCpy $Components "$Components,ftdi"
SectionEnd
Section "CH341 / CH343 USB 转串口" SEC_SERIAL
  StrCpy $Components "$Components,serial"
SectionEnd
Section "串口仪器（NI-VISA）" SEC_VISA
  StrCpy $Components "$Components,visa"
SectionEnd
Section "PD 采集卡（NI-DAQmx）" SEC_DAQ
  StrCpy $Components "$Components,daq"
SectionEnd
Section "Ophir USB 功率计（StarLab + COM）" SEC_OPHIR
  StrCpy $Components "$Components,ophir"
SectionEnd
Section "Coherent PowerMax USB 功率计" SEC_COHERENT
  StrCpy $Components "$Components,coherent"
SectionEnd

Section -Install
  ; Retain an admin-write-only cache so a required NI reboot cannot lose the
  ; installers when NSIS removes its temporary directory. Do not use ProgramData
  ; or a user's temp folder as the source of an elevated resume task.
  CreateDirectory "$COMMONFILES64\ARP\Setup\${APP_VERSION}"
  ClearErrors
  GetTempFileName $SetupCache "$COMMONFILES64\ARP\Setup\${APP_VERSION}"
  ${If} ${Errors}
    MessageBox MB_ICONSTOP|MB_OK "无法创建安装缓存。请检查系统盘可用空间和目录权限。"
    SetErrorLevel 1
    Abort
  ${EndIf}
  Delete "$SetupCache"
  CreateDirectory "$SetupCache"
  SetOutPath "$SetupCache"
  File /oname=PowerTest.msi "${APP_MSI}"
  File /oname=install_full_bundle.ps1 "${INSTALL_SCRIPT}"
  File /oname=full_bundle_helpers.ps1 "${HELPER_SCRIPT}"
  SetOutPath "$SetupCache\drivers"
  File /r "${DRIVER_ROOT}\*.*"

  ${If} $Components == ""
    StrCpy $Components "none"
  ${Else}
    StrCpy $Components $Components "" 1
  ${EndIf}
  DetailPrint "正在安装 Power Test、WebView2 和所选设备驱动。详细进度见下方。"
  ; NSIS is 32-bit. Sysnative launches 64-bit PowerShell for 64-bit COM, registry,
  ; NI and driver operations; SetRegView alone does not disable WOW64 redirection.
  nsExec::ExecToLog '"$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "$SetupCache\install_full_bundle.ps1" -PayloadRoot "$SetupCache\drivers" -AppMsiPath "$SetupCache\PowerTest.msi" -AppInstallDir "$INSTDIR" -Components "$Components"'
  Pop $0
  ${If} $0 == "20"
    StrCpy $FinishMessage "安装尚未完成。请重启 Windows，并使用本次安装的同一管理员账户登录，安装程序将自动继续。请勿删除安装缓存。"
    SetRebootFlag true
    SetErrorLevel 20
  ${ElseIf} $0 == "3010"
    StrCpy $FinishMessage "Power Test、WebView2 和所选驱动已安装。请重启 Windows 后连接设备并启动软件。"
    SetRebootFlag true
    SetErrorLevel 3010
  ${ElseIf} $0 != "0"
    MessageBox MB_ICONSTOP|MB_OK "安装未完成（错误 $0）。请查看 C:\ProgramData\Power Test\InstallerLogs 中的日志。修复问题后重新运行同一个安装包。"
    SetErrorLevel 1
    Abort
  ${Else}
    SetErrorLevel 0
  ${EndIf}
  WriteRegStr HKLM "Software\ARP\Power Test" "SetupCache" "$SetupCache"
SectionEnd

Function .onGUIEnd
  System::Call 'kernel32::CloseHandle(p $SetupMutex)'
FunctionEnd

Function .onSelChange
  ${If} ${SectionIsSelected} ${SEC_FTDI}
  ${OrIf} ${SectionIsSelected} ${SEC_SERIAL}
  ${OrIf} ${SectionIsSelected} ${SEC_COHERENT}
    !insertmacro SelectSection ${SEC_VISA}
  ${EndIf}
FunctionEnd
