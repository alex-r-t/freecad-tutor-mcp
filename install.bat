@echo off
REM Double-clickable Windows 11 installer launcher for FreeCAD Tutor MCP.
REM Uses -ExecutionPolicy Bypass so PowerShell's default Restricted policy does not block setup.
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE% EQU 0 (
    echo Setup finished successfully.
) else (
    echo Setup encountered an error ^(exit code %EXITCODE%^). See messages above.
)
pause
exit /b %EXITCODE%
