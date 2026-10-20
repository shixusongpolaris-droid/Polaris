# 智修云 API 契约 v1.0

后端默认地址 `http://127.0.0.1:8000`，前端与 API 同源。所有响应为 JSON；时间格式 `YYYY-MM-DD HH:MM:SS`。
状态 code：`submitted` 已提交 / `understood` 已理解 / `assigned` 待接单 / `accepted` 已接单 / `processing` 处理中 / `completed` 已完工 / `returned` 退回整改 / `closed` 已闭环 / `cancelled` 已取消。

## 0. 健康检查

`GET /api/health` → `{"status":"ok","version":"1.0.0","db":"...","history_orders":3000}`

## 1. 看板总览

`GET /api/dashboard/summary?days=30`

```json
{
  "generated_at": "2026-10-20 14:00:00",
  "historical": {
    "total": 3000, "closed_rate": 94.2, "timeout_rate": 8.8,
    "avg_duration": 128.4, "avg_response": 16.2, "satisfaction": 4.42,
    "auto_dispatch_rate": 93.6, "period_days": 180
  },
  "live": {
    "total": 1, "open": 0, "closed": 1, "today": 1,
    "timeout": 1, "returned": 1, "escalated": 1, "closed_rate": 100.0
  },
  "agent": {
    "understanding_accuracy": 94.7, "dispatch_accuracy": 91.8,
    "verification_pass_rate": 88.5, "manual_reassign_rate": 6.2,
    "avg_decision_ms": 12.3
  },
  "trend": [{"date": "2026-09-21", "orders": 18, "closed": 17, "timeout": 2}],
  "types": [{"name": "水管漏水", "value": 436}],
  "facilities": [{"name": "给排水", "value": 812}],
  "buildings": [{"name": "5栋", "value": 226, "timeout_rate": 10.2}],
  "workers": [{"name": "王建国", "trade": "水电工", "orders": 300,
               "avg_duration": 72.0, "satisfaction": 4.7, "timeout_rate": 5.1}],
  "urgency": [{"name": "紧急", "value": 380}],
  "hourly": [{"hour": 0, "orders": 45}],
  "recent": [{"id": "WO202610200001", "repair_type": "水管爆裂", "status": "closed",
              "status_label": "已闭环", "urgency": "紧急", "location": "5栋1单元",
              "worker_name": "王建国", "created_at": "2026-10-20 13:00:00"}]
}
```

字段缺失时前端要容错（空数组）。

## 2. 工单

`GET /api/tickets?status=open|all|<code>&urgency=&q=&limit=50&offset=0`
→ `{"items":[...ticket...],"total":123,"limit":50,"offset":0}`

`GET /api/tickets/{ticket_id}` → `{"ticket":{...},"events":[...],"logs":[...],"warnings":[...]}`

Ticket 关键字段：`id, trace_id, description, repair_type, facility_type, building, unit,
location, urgency, urgency_score, status, status_label, status_color, worker_id, worker_name,
trade, priority, sla_minutes, deadline, auto_dispatched, reassigned, escalated, created_at,
assigned_at, accepted_at, completed_at, closed_at, response_minutes, duration_minutes,
is_timeout, photos_count, result_note, verification_verdict, verification_note`。

`POST /api/tickets`
```json
{"description":"5栋1单元水管爆裂，楼道积水","reporter_name":"王女士",
 "reporter_phone":"13800000000","building":"5栋","unit":"1单元","room":""}
```
→ `{"ticket":{...},"events":[...],"logs":[...],"warnings":[]}`（HTTP 201）

`POST /api/tickets/{id}/accept` body `{"worker_id":"W003"}`（可省略，默认工单已派维修工）
→ `{"ticket":..., "trace":{...}}`

`POST /api/tickets/{id}/complete` body `{"result_note":"...","photos_count":2}`
→ `{"ticket":..., "verification":{...}, "trace":{...}}`。完工后自动触发闭环验证；不通过则状态 `returned`。

`POST /api/tickets/{id}/reassign` body `{"worker_id":"W004","reason":"原维修工负载过高"}`
→ `{"ticket":..., "trace":{...}}`（人工接管，写决策日志）

`GET /api/workers` → `{"items":[{id,name,trade,skills,zone,level,rating,
avg_duration_min,max_daily,active_tickets,load_ratio}]}`

`GET /api/warnings?status=open` → `{"items":[...]}`

## 3. 监控

`POST /api/monitor/scan` body 可空 `{"now":"2026-10-20 14:00:00"}`（可选，便于测试）
→ `{"scanned":3,"warnings_created":2,"escalated":1,"items":[...]}`

## 4. 决策日志

`GET /api/logs?agent=&ticket_id=&limit=100&offset=0`
→ `{"items":[{"id":1,"trace_id":"...","ticket_id":"...","agent":"dispatch",
"agent_name":"派单决策 Agent","action":"dispatch_decision","input_summary":"...",
"output":{...},"rationale":"技能完全匹配(+38)...","confidence":0.93,
"duration_ms":1.2,"created_at":"..."}],"total":10}`

Agent code：`understanding` / `dispatch` / `monitoring` / `verification` / `coordinator`。

## 5. 离线分析

`GET /api/analytics/agent-evaluation`
→ `{"methodology":"...","sample_size":600,"metrics":{...},"per_type":[...],"confusion_matrix":{...}}`

`GET /api/analytics/type-stats` → 报修类型、平均处理时长、超时率、SLA 等。

## 6. 完整演示（核心）

`POST /api/demo/run`（每次会清理上一条演示工单）
```json
{
  "ticket": {...},
  "steps": [
    {"seq":1,"time":"T+00:00","agent":"coordinator","agent_name":"工单编排中心",
     "event_type":"ticket_created","title":"工单创建","summary":"业主提交...",
     "status":"submitted","status_label":"已提交","payload":{}}
  ],
  "metrics_before": {...},
  "metrics_after": {...},
  "warnings": [...],
  "case": {"title":"5栋1单元水管爆裂","sla_minutes":30,"worker":"王建国"}
}
```

`GET /api/demo/latest` → 最近一条 `is_demo=1` 的 trace，结构同工单详情；没有则 404。

## 7. 静态资源

`GET /` 返回看板页面。报告文件：`/reports/历史工单分析报告.md`（纯文本），图片：`/reports/figures/*.png`。

## 错误格式

HTTP 4xx/5xx → `{"detail":"错误说明"}`。`404` 用于工单不存在。
