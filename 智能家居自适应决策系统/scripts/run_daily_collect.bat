@echo off
REM ============================================================
REM Daily sensor data collection (called by Windows Task Scheduler)
REM ------------------------------------------------------------
REM Keep CODE ASCII-only and keep CRLF line endings: cmd.exe reads
REM .bat with the OEM code page and does not tolerate LF-only lines.
REM The project path below contains Chinese characters; that is fine
REM because this file is saved in the Windows ANSI code page.
REM
REM Use:  run_daily_collect.bat [minutes] [port]
REM Output:
REM   data\raw\daily_<date>.csv          collected data
REM   data\raw\daily_<date>_events.csv   event log
REM   data\logs\collect_<date>.log       run log
REM ============================================================
setlocal

set STOP_AFTER=2026-10-16

set MINUTES=%~1
if "%MINUTES%"=="" set MINUTES=1420

set PORT=%~2
if "%PORT%"=="" set PORT=COM3

set PROJ=D:\Dev\Projects\Polaris\智能家居自适应决策系统
set PY=%PROJ%\.venv\Scripts\python.exe

if not exist "%PROJ%\data\logs" mkdir "%PROJ%\data\logs"

for /f "usebackq delims=" %%d in (`powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"`) do set TODAY=%%d

for /f "usebackq delims=" %%r in (`powershell -NoProfile -Command "if ([datetime]::Parse('%TODAY%') -gt [datetime]::Parse('%STOP_AFTER%')) { 'STOP' } else { 'GO' }"`) do set DECISION=%%r

set LOG=%PROJ%\data\logs\collect_%TODAY%.log

if /i "%DECISION%"=="STOP" (
    echo [%DATE% %TIME%] past cutoff %STOP_AFTER% today=%TODAY% skip >> "%LOG%"
    echo past cutoff %STOP_AFTER%, skip collection.
    endlocal
    exit /b 0
)

echo ============================================================ >> "%LOG%"
echo [%DATE% %TIME%] start minutes=%MINUTES% port=%PORT% cutoff=%STOP_AFTER% >> "%LOG%"

"%PY%" "%PROJ%\scripts\collect_data.py" --port %PORT% --no-keyboard --minutes %MINUTES% --prefix daily_ --note "scheduled daily collection" >> "%LOG%" 2>&1

echo [%DATE% %TIME%] done exitcode=%ERRORLEVEL% >> "%LOG%"

endlocal
