# 社区守护与停车管理 API 契约（v1.1 扩展）

> 在原有工单 API 基础上新增两组接口。前端新页面使用；与原有 `/api/*` 共用同一 FastAPI 服务。
> 时间格式：`YYYY-MM-DD HH:MM:SS`。金额单位：元。

## 一、小区守护 `/api/community`

### 1. 总览

`GET /api/community/overview`

```json
{
  "generated_at": "2026-10-20 15:00:00",
  "stats": {
    "residents": 240,
    "home": 206,
    "away": 34,
    "inactive_3d": 9,
    "inactive_7d": 2,
    "open_alerts": 7,
    "critical_alerts": 2,
    "today_access_events": 318
  },
  "risk_distribution": [
    {"name": "正常", "value": 210, "color": "#16a34a"},
    {"name": "关注", "value": 18, "color": "#f59e0b"},
    {"name": "预警", "value": 8, "color": "#f97316"},
    {"name": "紧急", "value": 4, "color": "#ef4444"}
  ],
  "trend": [
    {"date": "2026-10-14", "access": 330, "alerts": 2}
  ],
  "building_ranking": [
    {"building": "12栋", "inactive": 4, "residents": 20}
  ],
  "alerts": [
    {
      "id": 1,
      "resident_id": "R0012",
      "resident_name": "王秀兰",
      "location": "12栋2单元301",
      "tags": ["独居老人", "慢病"],
      "level": "L3",
      "level_label": "三级紧急",
      "inactive_days": 8.2,
      "threshold_days": 2.0,
      "message": "独居老人已 8.2 天未出门，超过关注阈值 2 天，建议立即上门探访",
      "status": "open",
      "status_label": "待处理",
      "created_at": "2026-10-20 14:30:00",
      "ack_at": null,
      "ack_by": null
    }
  ],
  "recent_events": [
    {
      "id": 101,
      "resident_id": "R0012",
      "resident_name": "王秀兰",
      "location": "12栋2单元301",
      "event_type": "entry",
      "event_label": "进门",
      "occurred_at": "2026-10-19 18:20:00",
      "source": "单元门禁"
    }
  ]
}
```

### 2. 住户列表

`GET /api/community/residents?risk=&presence=&q=&limit=50&offset=0`

`risk` 可传 `normal/watch/warning/critical`；`presence` 可传 `home/away`；`q` 搜姓名/楼栋/房号。

```json
{
  "items": [
    {
      "id": "R0012",
      "name": "王秀兰",
      "gender": "女",
      "age": 78,
      "phone": "138****1234",
      "building": "12栋",
      "unit": "2单元",
      "room": "301",
      "location": "12栋2单元301",
      "tags": ["独居老人", "慢病"],
      "family_contact": "王先生 139****8899",
      "presence": "home",
      "presence_label": "在家",
      "last_exit_at": "2026-10-12 08:10:00",
      "last_entry_at": "2026-10-19 18:20:00",
      "last_seen_at": "2026-10-19 18:20:00",
      "inactive_days": 8.2,
      "threshold_days": 2.0,
      "risk_level": "critical",
      "risk_label": "紧急",
      "away_until": null,
      "absence_reason": null,
      "open_alert": {"id": 1, "level": "L3", "message": "..."}
    }
  ],
  "total": 240,
  "limit": 50,
  "offset": 0,
  "risk_counts": {"normal": 210, "watch": 18, "warning": 8, "critical": 4}
}
```

### 3. 住户详情

`GET /api/community/residents/{resident_id}`

```json
{"resident": {...同列表字段...}, "events": [...最近30条门禁...], "alerts": [...该住户预警...]}
```

### 4. 登记出入

`POST /api/community/access`

```json
{"resident_id": "R0012", "event_type": "exit", "source": "单元门禁", "occurred_at": null}
```

`event_type`：`entry` / `exit`；`occurred_at` 可省略，默认当前时间。
→ `{"resident": {...}, "event": {...}, "alerts_resolved": 1}`

### 5. 自动巡检

`POST /api/community/scan`

body 可空，可选 `{"now":"2026-10-20 15:00:00"}`（便于演示/测试）。

→ `{"scanned":240,"alerts_created":3,"alerts":[...],"scanned_at":"..."}`

### 6. 预警

`GET /api/community/alerts?status=open&limit=100` → `{"items":[...],"total":7}`

`POST /api/community/alerts/{id}/ack` body `{"operator":"物业值班员"}` → `{"alert":{...}}`

## 二、停车管理 `/api/parking`

### 1. 看板

`GET /api/parking/dashboard`

```json
{
  "generated_at": "2026-10-20 15:00:00",
  "lot": {"name": "地下一层停车场", "total_spaces": 120, "occupied": 37,
          "available": 83, "occupancy_rate": 30.8},
  "stats": {
    "today_revenue": 386.0, "month_revenue": 12840.0,
    "today_entries": 52, "today_exits": 48, "today_paid_sessions": 44,
    "avg_duration_minutes": 96.3, "monthly_pass": 68, "temporary_pass": 42,
    "unpaid_count": 3, "unpaid_amount": 45.0
  },
  "revenue_trend": [{"date": "2026-10-01", "revenue": 420.0, "sessions": 51}],
  "hourly_entries": [{"hour": 8, "entries": 12}],
  "payment_mix": [{"name": "微信", "value": 18}, {"name": "支付宝", "value": 12},
                  {"name": "月卡", "value": 8}, {"name": "现金", "value": 2}],
  "parked": [
    {"id": "PS202610200001", "plate": "粤B12345", "vehicle_type": "小型车",
     "owner_name": "李先生", "location": "8栋2单元", "entry_at": "2026-10-20 09:12:00",
     "duration_minutes": 348, "fee": 10.0, "status": "parked", "status_label": "停放中",
     "payment_method": null, "paid_at": null, "is_monthly": true, "space_no": "B1-023",
     "is_overstay": false}
  ],
  "recent_sessions": [
    {"id": "PS202610200002", "plate": "粤B67890", "vehicle_type": "新能源",
     "owner_name": "张女士", "location": "3栋1单元", "entry_at": "2026-10-20 08:30:00",
     "exit_at": "2026-10-20 12:10:00", "duration_minutes": 220, "fee": 8.0,
     "status": "unpaid", "status_label": "待缴费", "payment_method": null, "paid_at": null,
     "is_monthly": false, "space_no": "B1-041", "is_overstay": false}
  ],
  "rules": {
    "free_minutes": 30, "rate_per_hour": 2.0, "daily_cap": 30.0,
    "monthly_fee": 300.0, "description": "30 分钟内免费；超出后 2 元/小时，单日封顶 30 元；月卡 300 元/月"
  }
}
```

### 2. 车辆入场

`POST /api/parking/entry`

```json
{"plate": "粤B12345", "vehicle_type": "小型车"}
```

→ `{"session": {...}, "lot": {...}, "billing_note": "月卡车辆，出场不另收费"}`
车牌不存在时自动创建临时车辆；车位已满返回 400。

### 3. 车辆出场 / 计费

`POST /api/parking/exit`

```json
{"plate": "粤B12345", "payment_method": "微信"}
```

`payment_method` 可省略；省略时只计费并生成待缴费记录。

→ `{"session": {...}, "billing": {
  "duration_minutes": 220, "billable_hours": 4, "free_minutes": 30,
  "rate_per_hour": 2.0, "daily_cap": 30.0, "fee": 8.0,
  "is_monthly": false, "breakdown": "停车 3 小时 40 分，免费 30 分钟，计费 4 小时 × 2 元 = 8 元"
}}`

### 4. 缴费

`POST /api/parking/sessions/{session_id}/pay` body `{"payment_method":"支付宝"}`
→ `{"session": {...}}`

### 5. 记录与车辆

`GET /api/parking/sessions?status=parked|unpaid|paid&limit=100&offset=0`
→ `{"items":[...],"total":0}`

`GET /api/parking/vehicles?q=&limit=100` → `{"items":[...],"total":0}`

## 三、Agent 日志扩展

新增 Agent code（写入原 `/api/logs`）：

- `community_guard` / 小区守护 Agent：`action=inactivity_scan`，记录巡检人数、生成预警、风险判定依据。
- `parking` / 停车收费 Agent：`action=parking_charge`，记录车牌、时长、免费时长、计费小时、费率、封顶、应收金额。

## 四、前端页面

- `#community` 小区守护：KPI、风险分布、7日出入/预警趋势、楼栋未出门排名、预警列表（可确认）、住户表格（筛选/搜索/登记外出/详情）。
- `#parking` 停车管理：KPI、车位占用、14日收入趋势、24小时入场、支付方式、在停车辆（模拟出场）、最近记录（待缴费可收费）、模拟入场。

## 五、住户画像与档案录入（v1.2）

### 5.1 画像字段

`GET /api/community/residents`、详情接口的每条住户增加：

```json
{
  "care_level": "特殊关怀",
  "persona_label": "独居长者 / 高龄老人 / 慢病照护",
  "profile": {
    "persona": ["独居长者", "高龄老人", "慢病照护"],
    "persona_label": "独居长者 / 高龄老人 / 慢病照护",
    "care_level": "特殊关怀",
    "summary": "王秀兰，82 岁，独居，高血压，行动自如；当前 9.2 天未出门，关怀等级为「特殊关怀」。",
    "risk_factors": ["独居，缺少日常照应", "高龄，行动与应急能力下降", "慢病管理，需要用药/复诊提醒"],
    "recommended_actions": ["每日电话问候", "每周至少一次上门探访", "联系家属确认近况", "检查居家安全隐患"],
    "completeness": 100,
    "living_status": "独居",
    "health_status": "高血压",
    "mobility": "行动自如",
    "family_members": "子女同城，周末探望",
    "service_needs": ["代购药品", "复诊提醒"],
    "habits": "每天上午在小区散步",
    "notes": "独居老人；慢病",
    "emergency_contact": "子女同城，周末探望 138****1234",
    "updated_at": "2026-10-20 15:00:00"
  }
}
```

`care_level` 可选值：`常规`、`日常关注`、`重点关注`、`特殊关怀`；传空字符串或“自动评估”表示由画像 Agent 自动计算。

### 5.2 新增住户

`POST /api/community/residents` → 201

```json
{
  "name": "王秀兰",
  "gender": "女",
  "age": 82,
  "phone": "13800001111",
  "building": "12栋",
  "unit": "2单元",
  "room": "301",
  "tags": ["独居老人", "高龄", "慢病"],
  "family_contact": "女儿 139****8899",
  "emergency_contact": "女儿 139****8899",
  "care_level": "",
  "profile": {
    "living_status": "独居",
    "health_status": "高血压",
    "mobility": "行动自如",
    "family_members": "女儿同城，周末探望",
    "service_needs": ["代购药品", "每日问候"],
    "habits": "每天上午在小区散步",
    "notes": "独居，需要定期关怀"
  }
}
```

→ `{"resident": {...含 profile...}}`

### 5.3 编辑画像/档案

`PUT /api/community/residents/{resident_id}`，请求体字段均可选（PATCH 语义）：

```json
{
  "age": 83,
  "tags": ["独居老人", "高龄", "慢病", "行动不便"],
  "emergency_contact": "女儿 139****8899",
  "care_level": "特殊关怀",
  "profile": {
    "living_status": "独居",
    "health_status": "高血压、糖尿病",
    "mobility": "行动不便",
    "family_members": "女儿同城，周末探望",
    "service_needs": ["代购药品", "上门探访", "复诊陪同"],
    "habits": "较少出门",
    "notes": "近期腿脚不便"
  }
}
```

→ `{"resident": {...更新后的画像...}}`

### 5.4 前端交互

- 小区守护页头提供「新增住户」入口。
- 住户详情抽屉提供「编辑画像」按钮，打开同一个表单。
- 表单字段：姓名、性别、年龄、电话、楼栋/单元/房号、标签（多选）、居住状态、健康状况、行动能力、家属信息、紧急联系人、服务需求、备注、关怀等级（自动评估/常规/日常关注/重点关注/特殊关怀）。
- 提交后调用 POST/PUT，刷新列表、KPI、画像抽屉，并 toast 提示。
