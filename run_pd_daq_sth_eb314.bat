@echo off
setlocal

cd /d "%~dp0"
set "PYTHON_EXE=D:\anaconda\envs\sth_eb314\python.exe"

if exist "%PYTHON_EXE%" (
    "%PYTHON_EXE%" "%~dp0tools\pd_daq_mvp.py"
) else (
    conda run -n sth_eb314 python "%~dp0tools\pd_daq_mvp.py"
)

if errorlevel 1 pause
endlocal
