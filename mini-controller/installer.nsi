Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"

Name "CH341 Mini Controller"
OutFile "${OUTPUT_FILE}"
InstallDir "$PROGRAMFILES64\CH341 Mini Controller"
InstallDirRegKey HKLM "Software\CGHX\CH341Mini" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show
VIProductVersion "0.1.0.0"
VIAddVersionKey "ProductName" "CH341 Mini Controller"
VIAddVersionKey "FileDescription" "CH341 Mini Controller 一键安装包"
VIAddVersionKey "FileVersion" "0.1.0"
VIAddVersionKey "LegalCopyright" ""

!define MUI_ABORTWARNING
!define MUI_ABORTWARNING_TEXT "终止安装？"
!define MUI_ICON "${APP_ICON}"
!define MUI_UNICON "${APP_ICON}"
!define MUI_FINISHPAGE_TEXT "CH341 Mini Controller 及 CH341 I²C 驱动安装完成。\r\n\r\n已创建桌面和开始菜单快捷方式。"
!define MUI_DIRECTORYPAGE_TEXT_TOP "设置软件安装目录。"
!define MUI_UNCONFIRMPAGE_TEXT_TOP "卸载 CH341 Mini Controller。\r\n共享 CH341 驱动将保留。"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP|MB_OK "系统要求：Windows 10/11（64 位）。"
    Abort
  ${EndIf}
  SetRegView 64
  SetShellVarContext all
  FindWindow $0 "" "CH341 Mini Controller"
  ${If} $0 != 0
    MessageBox MB_ICONSTOP|MB_OK "程序运行中。关闭程序后重试安装。"
    Abort
  ${EndIf}
FunctionEnd

Function .onInstSuccess
  IfRebootFlag 0 +2
    SetErrorLevel 3010
FunctionEnd

Function un.onInit
  FindWindow $0 "" "CH341 Mini Controller"
  ${If} $0 != 0
    MessageBox MB_ICONSTOP|MB_OK "程序运行中。完成下电并关闭程序后重试卸载。"
    Abort
  ${EndIf}
FunctionEnd

Section "CH341 Mini Controller"
  ; No hardware is opened by setup. Stage/install only the signed WCH INF package.
  InitPluginsDir
  SetOutPath "$PLUGINSDIR\ch341"
  File "${DRIVER_ROOT}\*.*"
  DetailPrint "正在安装 CH341 I²C 驱动…"
  ${DisableX64FSRedirection}
  nsExec::ExecToLog '"$SYSDIR\pnputil.exe" /add-driver "$PLUGINSDIR\ch341\CH341WDM.INF" /install'
  Pop $0
  ${EnableX64FSRedirection}
  ${If} $0 == 3010
  ${OrIf} $0 == 1641
    SetRebootFlag true
  ${ElseIf} $0 != 0
    MessageBox MB_ICONSTOP|MB_OK "CH341 驱动安装失败（错误码 $0）。\r\n安装已终止；详见安装日志。"
    SetErrorLevel 1
    Abort
  ${EndIf}
  SetOutPath "$INSTDIR"
  File "${APP_EXE}"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\CH341 Mini Controller.lnk" "$INSTDIR\CH341Mini.exe"
  CreateDirectory "$SMPROGRAMS\CH341 Mini Controller"
  CreateShortcut "$SMPROGRAMS\CH341 Mini Controller\CH341 Mini Controller.lnk" "$INSTDIR\CH341Mini.exe"
  CreateShortcut "$SMPROGRAMS\CH341 Mini Controller\卸载.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "Software\CGHX\CH341Mini" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CH341Mini" "DisplayName" "CH341 Mini Controller"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CH341Mini" "DisplayVersion" "0.1.0"
  DeleteRegValue HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CH341Mini" "Publisher"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CH341Mini" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CH341Mini" "DisplayIcon" "$INSTDIR\CH341Mini.exe"
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CH341Mini" "NoModify" 1
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CH341Mini" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetRegView 64
  SetShellVarContext all
  Delete "$DESKTOP\CH341 Mini Controller.lnk"
  Delete "$SMPROGRAMS\CH341 Mini Controller\CH341 Mini Controller.lnk"
  Delete "$SMPROGRAMS\CH341 Mini Controller\卸载.lnk"
  RMDir "$SMPROGRAMS\CH341 Mini Controller"
  Delete "$INSTDIR\CH341Mini.exe"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKLM "Software\CGHX\CH341Mini"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CH341Mini"
  ; Keep the shared Windows driver installed for other CH341 software/devices.
SectionEnd
