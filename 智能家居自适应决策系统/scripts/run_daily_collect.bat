@echo off
REM ============================================================
REM 每日传感器数据采集（供 Windows 计划任务调用）
REM ------------------------------------------------------------
REM 用法：
REM   run_daily_collect.bat [采集分钟数] [串口]
REM   默认：1420 分钟（约 23.7 小时）、COM3
REM
REM 输出：
REM   data\raw\daily_<日期>.csv          采集数据（跨零点自动分割文件）
REM   data\raw\daily_<日期>_events.csv   事件记录
REM   data\logs\collect_<日期>.log       运行日志
REM
REM 采集计划（2026-10-14 制定）：
REM   只采集两天，对比"工作日外出"与"全天在家"两种样本：
REM     10/15（工作日，用户外出）  白天低占用样本
REM     10/16（用户全天在家）      高占用完整样本
REM   到期后本脚本直接退出，不再采集。
REM   截止日期由 STOP_AFTER 控制（格式 YYYY-MM-DD）。
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

REM ---------- 用 PowerShell 做可靠的日期比较 ----------
REM 批处理的字符串比较在部分区域设置下不可靠（例如 2026-9-9 形式），
REM 这里把日期交给 PowerShell 的 DateTime 解析后比较。
for /f "usebackq delims=" %%d in (`powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd'"`) do set TODAY=%%d

for /f "usebackq delims=" %%r in (`powershell -NoProfile -Command "if ([datetime]::Parse('%TODAY%') -gt [datetime]::Parse('%STOP_AFTER%')) { 'STOP' } else { 'GO' }"`) do set DECISION=%%r

set LOG=%PROJ%\data\logs\collect_%TODAY%.log

if /i "%DECISION%"=="STOP" (
    echo [%DATE% %TIME%] 已超过采集截止日期 %STOP_AFTER%（今天 %TODAY%），本次不采集。 >> "%LOG%"
    echo 已超过采集截止日期 %STOP_AFTER%，本次不采集。
    endlocal
    exit /b 0
)

echo ============================================================ >> "%LOG%"
echo [%DATE% %TIME%] 开始采集  分钟数=%MINUTES%  串口=%PORT%  截止=%STOP_AFTER% >> "%LOG%"

"%PY%" "%PROJ%\scripts\collect_data.py" --port %PORT% --no-keyboard --minutes %MINUTES% --prefix daily_ --note "每日定时采集" >> "%LOG%" 2>&1

echo [%DATE% %TIME%] 采集结束  退出码=%ERRORLEVEL% >> "%LOG%"

endlocal
