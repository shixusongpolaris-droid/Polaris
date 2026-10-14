# ============================================================
# 移除「每日自动采集」计划任务（采集结束后运行）
# ------------------------------------------------------------
# 用法：
#   powershell -ExecutionPolicy Bypass -File remove_daily_task.ps1
#   powershell -ExecutionPolicy Bypass -File remove_daily_task.ps1 -KeepLogs
#
# 说明：
#   · 删除计划任务 SmartHome_DailyCollect
#   · 默认同时清理该任务产生的运行日志（data\logs\collect_*.log）
#   · 不会动采集到的数据（data\raw\*），数据需要你自己判断保留
#   · 这是「恢复原状」的完整操作，无需重装系统
# ============================================================

param(
    [string]$TaskName = 'SmartHome_DailyCollect',
    [switch]$KeepLogs          # 加此开关则保留日志
)

$ErrorActionPreference = 'Continue'
$ProjectDir = 'D:\Dev\Projects\Polaris\智能家居自适应决策系统'

Write-Output '=========================================='
Write-Output ' 移除每日采集计划任务'
Write-Output '=========================================='

# ---------- 1. 停止正在运行的任务实例 ----------
$running = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($running) {
    if ($running.State -eq 'Running') {
        Write-Output '  任务正在运行，先停止…'
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
} else {
    Write-Output '  [提示] 未找到该计划任务（可能已删除）'
}

# ---------- 2. 删除任务 ----------
$t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($t) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Start-Sleep -Seconds 1
    $check = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($check) {
        Write-Output '  [失败] 任务仍然存在，请用管理员权限重试'
    } else {
        Write-Output '  [OK] 计划任务已删除'
    }
}

# ---------- 3. 清理该任务的运行日志 ----------
if (-not $KeepLogs) {
    $logDir = Join-Path $ProjectDir 'data\logs'
    $logs = Get-ChildItem $logDir -Filter 'collect_*.log' -ErrorAction SilentlyContinue
    if ($logs) {
        $logs | ForEach-Object {
            Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
        }
        Write-Output ('  [OK] 已清理 ' + $logs.Count + ' 个运行日志')
    } else {
        Write-Output '  [提示] 无运行日志需要清理'
    }
} else {
    Write-Output '  [跳过] 按参数要求保留日志'
}

# ---------- 4. 最终确认 ----------
Write-Output ''
$remain = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($remain) {
    Write-Output '  最终状态: 任务仍存在（未清理干净）'
} else {
    Write-Output '  最终状态: ✅ 系统中已无该计划任务，恢复原状完成'
}
Write-Output '=========================================='
