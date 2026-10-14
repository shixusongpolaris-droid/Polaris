# ============================================================
# 注册「每日自动采集」计划任务（Windows 计划任务，重启不丢失）
# ------------------------------------------------------------
# 用法：
#   powershell -ExecutionPolicy Bypass -File register_daily_task.ps1
#   powershell -ExecutionPolicy Bypass -File register_daily_task.ps1 -At 08:00 -Minutes 720 -Port COM3
#
# 说明：
#   · 每天在指定时间启动一次采集，持续指定分钟数，到点自动停止
#   · 采集数据写入 data\raw\daily_<日期>.csv
#   · 运行日志写入 data\logs\collect_<日期>.log
#   · 任务以「当前用户、登录时运行」方式注册，不需要管理员权限
# ============================================================

param(
    [string]$At       = '08:00',    # 每天启动时间
    [int]   $Minutes  = 720,        # 每次采集时长（分钟）
    [string]$Port     = 'COM3',
    [string]$TaskName = 'SmartHome_DailyCollect'
)

$ErrorActionPreference = 'Stop'

$ProjectDir = 'D:\Dev\Projects\Polaris\智能家居自适应决策系统'
$BatFile    = Join-Path $ProjectDir 'scripts\run_daily_collect.bat'
$PyExe      = Join-Path $ProjectDir '.venv\Scripts\python.exe'

Write-Output '=========================================='
Write-Output ' 注册每日采集计划任务'
Write-Output '=========================================='

# ---------- 前置检查 ----------
if (-not (Test-Path $BatFile)) {
    Write-Output ('[错误] 找不到采集脚本: ' + $BatFile)
    exit 1
}
if (-not (Test-Path $PyExe)) {
    Write-Output ('[错误] 找不到 Python 环境: ' + $PyExe)
    exit 1
}
Write-Output ('  采集脚本 : ' + $BatFile)
Write-Output ('  Python   : ' + $PyExe)
Write-Output ('  启动时间 : 每天 ' + $At)
Write-Output ('  采集时长 : ' + $Minutes + ' 分钟')
Write-Output ('  串口     : ' + $Port)
Write-Output ''

# ---------- 删除同名旧任务 ----------
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Output '  删除已存在的同名任务…'
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# ---------- 动作：运行批处理 ----------
# 参数用双引号包围路径，避免中文路径中的空格问题
$argLine = '/c "' + $BatFile + '" ' + $Minutes + ' ' + $Port
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $argLine -WorkingDirectory $ProjectDir

# ---------- 触发器：每天定时 ----------
$trigger = New-ScheduledTaskTrigger -Daily -At $At

# ---------- 设置 ----------
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
$settings.ExecutionTimeLimit = 'PT24H'

# ---------- 主体：当前用户、交互式登录 ----------
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

# ---------- 注册 ----------
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description '项目每日传感器数据采集（ESP32 + 面包板传感器）' | Out-Null

# ---------- 验证 ----------
$t    = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Output '  [OK] 任务已注册'
Write-Output ('    任务名称 : ' + $t.TaskName)
Write-Output ('    状态     : ' + $t.State)
Write-Output ('    下次运行 : ' + $info.NextRunTime)
Write-Output ''
Write-Output '  管理命令：'
Write-Output ('    查看   : Get-ScheduledTask -TaskName ' + $TaskName)
Write-Output ('    立即跑 : Start-ScheduledTask -TaskName ' + $TaskName)
Write-Output ('    停止   : Stop-ScheduledTask -TaskName ' + $TaskName)
Write-Output ('    删除   : Unregister-ScheduledTask -TaskName ' + $TaskName + ' -Confirm:`$false')
Write-Output '=========================================='
