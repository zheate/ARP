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
!ifndef APP_VERSION
  !error "APP_VERSION is required"
!endif

Name "Power Test Setup with Driver Files"
OutFile "${OUTPUT_FILE}"
Icon "${APP_ICON}"
InstallDir "$PROGRAMFILES64\Power Test"
InstallDirRegKey HKLM "Software\ARP\Power Test" "InstallDir"
BrandingText "Power Test Offline Driver Files"
ShowInstDetails show
SetCompressor /SOLID lzma
SetCompressorDictSize 32

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=2052 "ProductName" "Power Test Setup with Driver Files"
VIAddVersionKey /LANG=2052 "CompanyName" "ARP"
VIAddVersionKey /LANG=2052 "LegalCopyright" "Copyright (C) ARP"
VIAddVersionKey /LANG=2052 "FileDescription" "Power Test application and offline driver files"
VIAddVersionKey /LANG=2052 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${APP_VERSION}"

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
    MessageBox MB_ICONSTOP|MB_OK "Power Test requires 64-bit Windows."
    Abort
  ${EndIf}
  SetRegView 64
  SetShellVarContext all
FunctionEnd

Section "Power Test and offline driver files" SEC_MAIN
  SetDetailsPrint both
  SetOutPath "$PLUGINSDIR\payload"
  File /oname=PowerTest.msi "${APP_MSI}"
  File /oname=install_full_bundle.ps1 "${INSTALL_SCRIPT}"

  SetOutPath "$PLUGINSDIR\payload\drivers"
  File /r "${DRIVER_ROOT}\*.*"

  DetailPrint "Installing Power Test and copying offline driver files. No hardware driver will be installed automatically."
  nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\payload\install_full_bundle.ps1" -PayloadRoot "$PLUGINSDIR\payload\drivers" -AppMsiPath "$PLUGINSDIR\payload\PowerTest.msi" -AppInstallDir "$INSTDIR" -DriverDestination "$APPDATA\Power Test\Drivers"'
  Pop $0

  ${If} $0 == "3010"
    WriteRegStr HKLM "Software\ARP\Power Test" "InstallDir" "$INSTDIR"
    WriteRegStr HKLM "Software\ARP\Power Test" "DriverDir" "$APPDATA\Power Test\Drivers"
    SetRebootFlag true
    MessageBox MB_ICONINFORMATION|MB_OK "Power Test was installed. No hardware driver was installed automatically. Driver files: $APPDATA\Power Test\Drivers. Read DRIVER-INSTALLATION.txt and install only the drivers you need."
  ${ElseIf} $0 != "0"
    MessageBox MB_ICONSTOP|MB_OK "Power Test installation failed (error $0). Log folder: C:\ProgramData\Power Test\InstallerLogs."
    Abort
  ${Else}
    WriteRegStr HKLM "Software\ARP\Power Test" "InstallDir" "$INSTDIR"
    WriteRegStr HKLM "Software\ARP\Power Test" "DriverDir" "$APPDATA\Power Test\Drivers"
    MessageBox MB_ICONINFORMATION|MB_OK "Power Test was installed. No hardware driver was installed automatically. Driver files: $APPDATA\Power Test\Drivers. Read DRIVER-INSTALLATION.txt and install only the drivers you need."
  ${EndIf}
SectionEnd
