@echo off
REM ============================================================
REM 每日传感器数据采集（供 Windows 计划任务调用）
REM ------------------------------------------------------------
REM 用法：
REM   run_daily_collect.bat [采集分钟数] [串口]
REM   默认：720 分钟（12 小时）、COM3
REM
REM 输出：
REM   data\raw\daily_<日期>.csv          采集数据（跨零点自动分割文件）
REM   data\raw\daily_<日期>_events.csv   事件记录
REM   data\logs\collect_<日期>.log       运行日志
REM
REM 文件前缀 daily_ 用于区分定时任务与手动采集，避免混淆。
REM ============================================================
setlocal

set MINUTES=%~1
if "%MINUTES%"=="" set MINUTES=720

set PORT=%~2
if "%PORT%"=="" set PORT=COM3

set PROJ=D:\Dev\Projects\Polaris\智能家居自适应决策系统
set PY=%PROJ%\.venv\Scripts\python.exe

if not exist "%PROJ%\data\logs" mkdir "%PROJ%\data\logs"

for /f "tokens=1-3 delims=/- " %%a in ("%DATE%") do set TODAY=%%a-%%b-%%c
set LOG=%PROJ%\data\logs\collect_%TODAY%.log

echo ============================================================ >> "%LOG%"
echo [%DATE% %TIME%] 开始采集  分钟数=%MINUTES%  串口=%PORT% >> "%LOG%"

"%PY%" "%PROJ%\scripts\collect_data.py" --port %PORT% --no-keyboard --minutes %MINUTES% --prefix daily_ --note "每日定时采集" >> "%LOG%" 2>&1

echo [%DATE% %TIME%] 采集结束  退出码=%ERRORLEVEL% >> "%LOG%"

endlocal
