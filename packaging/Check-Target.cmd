@echo off
setlocal
cd /d "%~dp0"
set "ARP_CHECK_PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if exist "%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe" set "ARP_CHECK_PS=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
"%ARP_CHECK_PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Collect-OfflineTarget.ps1" -OutputPath "%~dp0offline-target-readiness.json"
set "ARP_CHECK_RESULT=%ERRORLEVEL%"
if not "%ARP_CHECK_RESULT%"=="0" (
    echo Check failed. Please send the error above.
) else (
    echo Report saved: "%~dp0offline-target-readiness.json"
)
pause
exit /b %ARP_CHECK_RESULT%
