@echo off
setlocal

cd /d "%~dp0"
set "CONDA_EXE=D:\anaconda\Scripts\conda.exe"

if exist "%CONDA_EXE%" (
    "%CONDA_EXE%" run -n sth_eb314 python "%~dp0main.py"
) else (
    conda run -n sth_eb314 python "%~dp0main.py"
)

if errorlevel 1 pause
endlocal
