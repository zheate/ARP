@echo off
setlocal
cd /d "%~dp0"
"%~dp0arp-python-x86_64-pc-windows-msvc.exe" --check-runtime --output "%~dp0runtime-compatibility.json" > "%~dp0runtime-check.log" 2>&1
set "ARP_RUNTIME_RESULT=%ERRORLEVEL%"
type "%~dp0runtime-check.log"
echo.
echo Please return runtime-compatibility.json and runtime-check.log.
echo Exit code: %ARP_RUNTIME_RESULT%
pause
exit /b %ARP_RUNTIME_RESULT%
