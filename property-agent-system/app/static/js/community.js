/* ==========================================================================
   community.js —— 小区守护页（#community）
   - 数据：GET /api/community/overview、/residents、/alerts、/residents/{id}
   - 操作：POST /api/community/scan、/access、/alerts/{id}/ack
   - 接口未就绪时：展示本地示例数据 + 明确「服务未就绪」提示与重试，绝不白屏
   - 页面每 60 秒静默刷新 overview + alerts，离开页面即释放定时器
   ========================================================================== */
(function () {
  'use strict';

  /* ======================================================================
     一、基础工具
     ====================================================================== */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function esc(str) {
    return String(str == null ? '' : str).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function icon(name, cls) {
    return '<svg class="icon' + (cls ? ' ' + cls : '') + '"><use href="#i-' + name + '"></use></svg>';
  }

  function arr(v) { return Array.isArray(v) ? v : []; }
  function num(v) { var n = Number(v); return isFinite(n) ? n : null; }
  function fmtNum(n) {
    var v = num(n);
    return v == null ? '—' : v.toLocaleString('zh-CN');
  }
  function fmtFixed(n, digits) {
    var v = num(n);
    return v == null ? '—' : v.toFixed(digits == null ? 1 : digits);
  }
  function fullTime(value) {
    if (!value) return '—';
    return String(value).replace('T', ' ').slice(0, 19);
  }
  function shortTime(value) {
    if (!value) return '—';
    var s = String(value).replace('T', ' ');
    return s.length >= 16 ? s.slice(5, 16) : s;
  }
  function debounce(fn, wait) {
    var timer = null;
    return function () {
      var args = arguments, ctx = this;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(ctx, args); }, wait);
    };
  }
  function toast(message, type) {
    if (window.UI && UI.toast) UI.toast(message, type);
  }
  function countUp(el, value, decimals) {
    if (!el) return;
    if (window.UI && UI.countUp) { UI.countUp(el, value, decimals); return; }
    var v = num(value);
    el.textContent = v == null ? '—' : (decimals ? v.toFixed(decimals) : v.toLocaleString('zh-CN'));
  }

  function setApiStatus(kind, text) {
    var chip = $('#apiStatusChip');
    if (chip) {
      chip.classList.remove('ok', 'off', 'down');
      if (kind) chip.classList.add(kind);
    }
    var label = $('#apiStatusText');
    if (label) label.textContent = text || '检测中';
  }

  function pad2(n) { return String(n).padStart(2, '0'); }
  function fmtDT(d) {
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) + ' ' +
      pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
  }
  function shiftDate(days, hour, minute) {
    var d = new Date();
    d.setDate(d.getDate() - (days || 0));
    d.setHours(hour == null ? 10 : hour, minute == null ? 0 : minute, 0, 0);
    return d;
  }
  function hoursAgo(h) { return fmtDT(new Date(Date.now() - h * 3600000)); }
  function rnd(seed) {
    var x = Math.sin(seed * 12.9898) * 43758.5453;
    return x - Math.floor(x);
  }

  var RISK = {
    normal: { label: '正常', color: '#16a34a', order: 3 },
    watch: { label: '关注', color: '#f59e0b', order: 2 },
    warning: { label: '预警', color: '#f97316', order: 1 },
    critical: { label: '紧急', color: '#ef4444', order: 0 }
  };
  var LEVEL_CLASS = { L3: 'l3', L2: 'l2', L1: 'l1' };
  var CARE_LEVELS = ['常规', '日常关注', '重点关注', '特殊关怀'];
  var CARE_CLASS = { '常规': 'regular', '日常关注': 'daily', '重点关注': 'focus', '特殊关怀': 'special' };
  var FORM_TAGS = ['独居老人', '慢病', '行动不便', '高龄', '残障'];
  var FORM_SERVICES = ['每日问候', '用药提醒', '陪同就医', '助餐', '助浴', '家政保洁', '紧急呼叫'];

  function carePill(level) {
    var lv = level || '自动评估';
    var cls = CARE_CLASS[lv] || 'auto';
    return '<span class="care-pill ' + cls + '">' + esc(lv) + '</span>';
  }

  function normalizeProfile(raw) {
    raw = raw || {};
    return {
      persona: arr(raw.persona),
      persona_label: raw.persona_label || '',
      care_level: raw.care_level || '',
      summary: raw.summary || '',
      risk_factors: arr(raw.risk_factors),
      recommended_actions: arr(raw.recommended_actions),
      completeness: num(raw.completeness),
      living_status: raw.living_status || '',
      health_status: raw.health_status || '',
      mobility: raw.mobility || '',
      family_members: raw.family_members || '',
      service_needs: arr(raw.service_needs),
      habits: raw.habits || '',
      notes: raw.notes || '',
      emergency_contact: raw.emergency_contact || '',
      updated_at: raw.updated_at || ''
    };
  }

  /* ======================================================================
     二、本地示例数据（接口未就绪时的降级展示）
     ====================================================================== */
  var NAMES = ['王秀兰', '李国强', '张桂芳', '刘志远', '陈美玲', '杨建华', '赵淑珍', '黄文斌', '周玉梅', '吴东海',
    '徐丽华', '孙德明', '马春霞', '朱国庆', '胡秀英', '郭伟民', '何静怡', '林大伟', '高淑芬', '罗建军',
    '梁小燕', '宋振华', '唐玉兰', '许志强', '韩月娥', '冯国平', '邓美华', '曹文杰', '彭桂英', '曾宪明',
    '肖红梅', '田晓东', '董雅琴', '潘志刚', '袁丽君', '蒋国华', '沈玉珍', '傅文龙', '范秀英', '崔志刚'];
  var BUILDINGS = ['1栋', '2栋', '3栋', '4栋', '5栋', '6栋', '7栋', '8栋', '9栋', '10栋', '11栋', '12栋'];
  var UNITS = ['1单元', '2单元', '3单元'];
  var TAG_POOL = ['独居老人', '慢病', '高龄', '残障', '孕产妇', '儿童', '长期出差', '空巢老人'];
  var mockCache = null;

  function mockProfileOf(resident) {
    var tags = resident.tags || [];
    var persona = [];
    if (tags.indexOf('独居老人') >= 0) persona.push('独居长者');
    if (tags.indexOf('空巢老人') >= 0) persona.push('空巢老人');
    if (tags.indexOf('高龄') >= 0 || (resident.age || 0) >= 80) persona.push('高龄老人');
    if (tags.indexOf('慢病') >= 0) persona.push('慢病照护');
    if (tags.indexOf('行动不便') >= 0) persona.push('行动不便');
    if (!persona.length) persona.push('普通住户');
    var level;
    if (resident.risk_level === 'critical' || resident.risk_level === 'warning') level = '特殊关怀';
    else if (resident.risk_level === 'watch') level = '重点关注';
    else if (persona[0] !== '普通住户') level = '日常关注';
    else level = '常规';
    var living = tags.indexOf('独居老人') >= 0 ? '独居' : tags.indexOf('空巢老人') >= 0 ? '空巢' : '与家人同住';
    var health = tags.indexOf('慢病') >= 0 ? '高血压' : '健康';
    var mobility = tags.indexOf('行动不便') >= 0 ? '行动不便' : (resident.age || 0) >= 85 ? '行动缓慢' : '行动自如';
    var needs = level === '特殊关怀' ? ['每日问候', '用药提醒', '紧急呼叫']
      : level === '重点关注' ? ['用药提醒', '陪同就医']
        : level === '日常关注' ? ['每日问候'] : [];
    var factors = [];
    if (living === '独居') factors.push('独居，缺少日常照应');
    if (persona.indexOf('高龄老人') >= 0) factors.push('高龄，行动与应急能力下降');
    if (health !== '健康') factors.push('慢病管理，需要用药/复诊提醒');
    if (mobility !== '行动自如') factors.push('行动不便，外出与就医需协助');
    if (!factors.length) factors.push('暂无显著风险标签');
    var actions = level === '特殊关怀' ? ['每日电话问候', '每周至少一次上门探访', '联系家属确认近况', '检查居家安全隐患']
      : level === '重点关注' ? ['每周电话问候', '每月上门探访', '提醒慢病用药与复诊']
        : level === '日常关注' ? ['每月电话问候', '节日前重点关怀']
          : ['按常规网格服务跟进'];
    var completeness = level === '特殊关怀' ? 100 : level === '重点关注' ? 90 : level === '日常关注' ? 80 : 70;
    return {
      persona: persona,
      persona_label: persona.join(' / '),
      care_level: level,
      summary: resident.name + '，' + resident.age + ' 岁，' + living + '，' + health + '，' + mobility +
        '；当前' + resident.inactive_days + '天未出门，关怀等级为「' + level + '」。',
      risk_factors: factors,
      recommended_actions: actions,
      completeness: completeness,
      living_status: living,
      health_status: health,
      mobility: mobility,
      family_members: resident.family_contact,
      service_needs: needs,
      habits: resident.presence === 'away' ? '近期外出中' : '日常居家为主',
      notes: tags.join('；'),
      emergency_contact: resident.family_contact,
      updated_at: resident.last_seen_at
    };
  }

  function buildMockResidents() {
    var list = [];
    for (var i = 0; i < 240; i++) {
      var risk = i < 4 ? 'critical' : i < 12 ? 'warning' : i < 30 ? 'watch' : 'normal';
      var r = rnd(i + 1);
      var inactive, threshold, tags, age, presence;
      if (risk === 'critical') {
        inactive = Math.round((6 + r * 6) * 10) / 10;
        threshold = 2;
        tags = ['独居老人', '慢病'];
        age = 72 + (i % 14);
        presence = 'home';
      } else if (risk === 'warning') {
        inactive = Math.round((3.2 + r * 2.6) * 10) / 10;
        threshold = 3;
        tags = i % 2 === 0 ? ['独居老人', '高龄'] : ['高龄'];
        age = 66 + (i % 18);
        presence = 'home';
      } else if (risk === 'watch') {
        inactive = Math.round((1.2 + r * 1.8) * 10) / 10;
        threshold = 5;
        tags = ['空巢老人'];
        age = 58 + (i % 20);
        presence = i % 4 === 0 ? 'away' : 'home';
      } else {
        inactive = Math.round(r * 1.1 * 10) / 10;
        threshold = 7;
        tags = i % 9 === 0 ? ['儿童'] : (i % 7 === 0 ? ['孕产妇'] : []);
        age = 24 + (i % 44);
        presence = i % 6 === 0 ? 'away' : 'home';
      }
      var building = BUILDINGS[i % BUILDINGS.length];
      var unit = UNITS[i % UNITS.length];
      var room = String(301 + (i % 8) * 2 + (i % 3));
      var name = NAMES[i % NAMES.length];
      var lastSeen = shiftDate(Math.floor(inactive), 8 + (i % 12), (i * 7) % 60);
      var lastExit = shiftDate(Math.floor(inactive) + 1, 8 + (i % 10), (i * 11) % 60);
      var lastEntry = presence === 'away' ? shiftDate(Math.floor(inactive) + 3, 18, (i * 5) % 60) : lastSeen;
      var resident = {
        id: 'R' + String(i + 1).padStart(4, '0'),
        name: name,
        gender: i % 2 === 0 ? '女' : '男',
        age: age,
        phone: '138****' + String(1000 + (i * 37) % 9000),
        building: building,
        unit: unit,
        room: room,
        location: building + unit + room,
        tags: tags,
        family_contact: NAMES[(i + 5) % NAMES.length] + ' 139****' + String(1000 + (i * 53) % 9000),
        presence: presence,
        presence_label: presence === 'home' ? '在家' : '外出',
        last_exit_at: fmtDT(lastExit),
        last_entry_at: fmtDT(lastEntry),
        last_seen_at: fmtDT(lastSeen),
        inactive_days: inactive,
        threshold_days: threshold,
        risk_level: risk,
        risk_label: RISK[risk].label,
        away_until: presence === 'away' ? fmtDT(shiftDate(-2, 20, 0)) : null,
        absence_reason: presence === 'away' ? (i % 2 ? '出差' : '探亲') : null,
        open_alert: null
      };
      var profile = mockProfileOf(resident);
      resident.emergency_contact = resident.family_contact;
      resident.care_level = profile.care_level;
      resident.persona_label = profile.persona_label;
      resident.profile = profile;
      list.push(resident);
    }
    return list;
  }

  var LEVEL_META = {
    L3: { label: '三级紧急', rank: 0 },
    L2: { label: '二级预警', rank: 1 },
    L1: { label: '一级提醒', rank: 2 }
  };

  function buildMockAlerts(residents) {
    var alerts = [];
    var id = 0;
    function push(resident, level, hours) {
      id += 1;
      var meta = LEVEL_META[level];
      var alert = {
        id: id,
        resident_id: resident.id,
        resident_name: resident.name,
        location: resident.location,
        tags: resident.tags.slice(),
        level: level,
        level_label: meta.label,
        inactive_days: resident.inactive_days,
        threshold_days: resident.threshold_days,
        message: resident.name + '已 ' + resident.inactive_days + ' 天未出门，超过' +
          (level === 'L3' ? '紧急阈值 ' : level === 'L2' ? '预警阈值 ' : '关注阈值 ') + resident.threshold_days + ' 天，建议立即上门探访',
        status: 'open',
        status_label: '待处理',
        created_at: hoursAgo(hours),
        ack_at: null,
        ack_by: null
      };
      alerts.push(alert);
      if (!resident.open_alert) resident.open_alert = { id: id, level: level, message: alert.message };
    }
    var criticals = residents.filter(function (r) { return r.risk_level === 'critical'; });
    var warnings = residents.filter(function (r) { return r.risk_level === 'warning'; });
    var watches = residents.filter(function (r) { return r.risk_level === 'watch'; });
    criticals.slice(0, 2).forEach(function (r, i) { push(r, 'L3', 1 + i * 2.5); });
    warnings.slice(0, 3).forEach(function (r, i) { push(r, 'L2', 4 + i * 3); });
    watches.slice(0, 2).forEach(function (r, i) { push(r, 'L1', 9 + i * 4); });
    return alerts;
  }

  function buildMock() {
    var residents = buildMockResidents();
    var alerts = buildMockAlerts(residents);
    var criticals = residents.filter(function (r) { return r.risk_level === 'critical'; });
    var home = residents.filter(function (r) { return r.presence === 'home'; }).length;
    var inactive3 = residents.filter(function (r) { return r.inactive_days >= 3; }).length;
    var inactive7 = residents.filter(function (r) { return r.inactive_days >= 7; }).length;

    var trend = [];
    for (var d = 6; d >= 0; d--) {
      var date = shiftDate(d, 0, 0);
      trend.push({
        date: date.getFullYear() + '-' + pad2(date.getMonth() + 1) + '-' + pad2(date.getDate()),
        access: 268 + Math.round(rnd(d + 11) * 96),
        alerts: Math.round(rnd(d + 21) * 4)
      });
    }

    var buildingMap = {};
    residents.forEach(function (r) {
      if (r.inactive_days < 3) return;
      buildingMap[r.building] = (buildingMap[r.building] || 0) + 1;
    });
    var buildingRanking = BUILDINGS.map(function (b) {
      return {
        building: b,
        inactive: buildingMap[b] || 0,
        residents: residents.filter(function (r) { return r.building === b; }).length
      };
    }).sort(function (a, b) { return b.inactive - a.inactive; }).filter(function (b) { return b.inactive > 0; }).slice(0, 8);

    var recentEvents = [];
    for (var e = 0; e < 12; e++) {
      var res = residents[(e * 13) % residents.length];
      var when = new Date(Date.now() - (e * 37 + 8) * 60000);
      recentEvents.push({
        id: 1000 + e,
        resident_id: res.id,
        resident_name: res.name,
        location: res.location,
        event_type: e % 3 === 0 ? 'exit' : 'entry',
        event_label: e % 3 === 0 ? '出门' : '进门',
        occurred_at: fmtDT(when),
        source: e % 4 === 0 ? '物业代登记' : '单元门禁'
      });
    }

    var counts = { normal: 0, watch: 0, warning: 0, critical: 0 };
    residents.forEach(function (r) { counts[r.risk_level] += 1; });

    var sortedAlerts = alerts.slice().sort(function (a, b) {
      return (LEVEL_META[a.level] ? LEVEL_META[a.level].rank : 9) - (LEVEL_META[b.level] ? LEVEL_META[b.level].rank : 9) ||
        String(b.created_at).localeCompare(String(a.created_at));
    });

    return {
      residents: residents,
      alerts: sortedAlerts,
      overview: {
        generated_at: fmtDT(new Date()),
        stats: {
          residents: residents.length,
          home: home,
          away: residents.length - home,
          inactive_3d: inactive3,
          inactive_7d: inactive7,
          open_alerts: alerts.length,
          critical_alerts: criticals.slice(0, 2).length,
          today_access_events: 318
        },
        risk_distribution: [
          { name: '正常', value: counts.normal, color: '#16a34a' },
          { name: '关注', value: counts.watch, color: '#f59e0b' },
          { name: '预警', value: counts.warning, color: '#f97316' },
          { name: '紧急', value: counts.critical, color: '#ef4444' }
        ],
        trend: trend,
        building_ranking: buildingRanking,
        alerts: sortedAlerts,
        recent_events: recentEvents
      },
      residentDetail: function (residentId) {
        var resident = null;
        residents.some(function (r) { if (r.id === residentId) { resident = r; return true; } return false; });
        if (!resident) return null;
        var events = [];
        var base = Date.now() - 6 * 3600000;
        for (var i = 0; i < 30; i++) {
          var isExit = i % 2 === 0;
          var when = new Date(base - i * (3.2 * 3600000 + (i % 5) * 1200000));
          events.push({
            id: 5000 + i,
            resident_id: resident.id,
            resident_name: resident.name,
            location: resident.location,
            event_type: isExit ? 'exit' : 'entry',
            event_label: isExit ? '出门' : '进门',
            occurred_at: fmtDT(when),
            source: i % 7 === 0 ? '物业代登记' : '单元门禁'
          });
        }
        return {
          resident: resident,
          events: events,
          alerts: alerts.filter(function (a) { return a.resident_id === resident.id; })
        };
      }
    };
  }

  function getMock() {
    if (!mockCache) mockCache = buildMock();
    return mockCache;
  }

  /* ======================================================================
     三、数据归一化
     ====================================================================== */
  function normalizeOverview(raw) {
    raw = raw || {};
    return {
      generated_at: raw.generated_at || '',
      stats: raw.stats || {},
      risk_distribution: arr(raw.risk_distribution),
      trend: arr(raw.trend),
      building_ranking: arr(raw.building_ranking),
      alerts: arr(raw.alerts),
      recent_events: arr(raw.recent_events)
    };
  }

  function normalizeResident(raw) {
    raw = raw || {};
    var risk = raw.risk_level || 'normal';
    var presence = raw.presence || 'home';
    return {
      id: String(raw.id || raw.resident_id || ''),
      name: raw.name || '—',
      gender: raw.gender || '',
      age: num(raw.age),
      phone: raw.phone || '—',
      building: raw.building || '',
      unit: raw.unit || '',
      room: raw.room || '',
      location: raw.location || [raw.building, raw.unit, raw.room].filter(Boolean).join('') || '—',
      tags: arr(raw.tags),
      family_contact: raw.family_contact || '—',
      presence: presence,
      presence_label: raw.presence_label || (presence === 'home' ? '在家' : '外出'),
      last_exit_at: raw.last_exit_at || null,
      last_entry_at: raw.last_entry_at || null,
      last_seen_at: raw.last_seen_at || null,
      inactive_days: num(raw.inactive_days),
      threshold_days: num(raw.threshold_days),
      risk_level: RISK[risk] ? risk : 'normal',
      risk_label: raw.risk_label || (RISK[risk] ? RISK[risk].label : '正常'),
      away_until: raw.away_until || null,
      absence_reason: raw.absence_reason || null,
      open_alert: raw.open_alert || null,
      care_level: raw.care_level || ((raw.profile && raw.profile.care_level) || ''),
      persona_label: raw.persona_label || ((raw.profile && raw.profile.persona_label) || ''),
      emergency_contact: raw.emergency_contact || '',
      profile: normalizeProfile(raw.profile)
    };
  }

  function normalizeAlert(raw) {
    raw = raw || {};
    var level = raw.level || 'L1';
    return {
      id: raw.id != null ? raw.id : String(raw.alert_id || ''),
      resident_id: String(raw.resident_id || ''),
      resident_name: raw.resident_name || '—',
      location: raw.location || '—',
      tags: arr(raw.tags),
      level: LEVEL_META[level] ? level : 'L1',
      level_label: raw.level_label || (LEVEL_META[level] ? LEVEL_META[level].label : ''),
      inactive_days: num(raw.inactive_days),
      threshold_days: num(raw.threshold_days),
      message: raw.message || '',
      status: raw.status || 'open',
      status_label: raw.status_label || (raw.status === 'ack' ? '已确认' : '待处理'),
      created_at: raw.created_at || null,
      ack_at: raw.ack_at || null,
      ack_by: raw.ack_by || null
    };
  }

  function normalizeResidentPage(raw) {
    raw = raw || {};
    return {
      items: arr(raw.items).map(normalizeResident),
      total: num(raw.total) != null ? num(raw.total) : arr(raw.items).length,
      limit: num(raw.limit) || 50,
      offset: num(raw.offset) || 0,
      risk_counts: raw.risk_counts || {}
    };
  }

  function normalizeAlertPage(raw) {
    raw = raw || {};
    return {
      items: arr(raw.items || raw.alerts).map(normalizeAlert),
      total: num(raw.total) != null ? num(raw.total) : arr(raw.items || raw.alerts).length
    };
  }

  /* ======================================================================
     四、页面状态
     ====================================================================== */
  var state = {
    loaded: false,
    loading: false,
    seq: 0,
    overview: null,
    overviewLive: false,
    residents: { items: [], total: 0, risk_counts: {}, live: false, filters: { risk: '', presence: '', q: '' } },
    alerts: { items: [], live: false },
    error: null,
    timer: null,
    acking: {},
    leaving: {},
    drawer: { id: null, detail: null, sample: false, error: null, seq: 0 },
    form: { open: false, mode: 'create', residentId: null, submitting: false, source: null }
  };

  /* ======================================================================
     五、请求封装（失败降级为示例数据 / 保留旧数据）
     ====================================================================== */
  async function fetchOverview() {
    try {
      var data = await API.communityOverview();
      if (!data || typeof data !== 'object') throw new Error('空响应');
      return { data: normalizeOverview(data), sample: false, error: null, live: true };
    } catch (e) {
      if (state.overview) return { data: state.overview, sample: !state.overviewLive, error: e, live: state.overviewLive };
      return { data: normalizeOverview(getMock().overview), sample: true, error: e, live: false };
    }
  }

  async function fetchResidents() {
    var filters = state.residents.filters;
    var params = { limit: 50, offset: 0 };
    if (filters.risk) params.risk = filters.risk;
    if (filters.presence) params.presence = filters.presence;
    if (filters.q) params.q = filters.q;
    try {
      var data = await API.communityResidents(params);
      if (!data || typeof data !== 'object') throw new Error('空响应');
      return { data: normalizeResidentPage(data), sample: false, error: null, live: true };
    } catch (e) {
      if (state.residents.live) {
        return { data: { items: state.residents.items, total: state.residents.total, risk_counts: state.residents.risk_counts }, sample: false, error: e, live: true };
      }
      return { data: mockResidentPage(filters), sample: true, error: e, live: false };
    }
  }

  async function fetchAlerts() {
    try {
      var data = await API.communityAlerts({ status: 'open', limit: 100 });
      if (!data || typeof data !== 'object') throw new Error('空响应');
      return { data: normalizeAlertPage(data), sample: false, error: null, live: true };
    } catch (e) {
      if (state.alerts.live) return { data: { items: state.alerts.items, total: state.alerts.items.length }, sample: false, error: e, live: true };
      return { data: { items: getMock().alerts.slice(), total: getMock().alerts.length }, sample: true, error: e, live: false };
    }
  }

  function mockResidentPage(filters) {
    var all = getMock().residents;
    var items = all.filter(function (r) {
      if (filters.risk && r.risk_level !== filters.risk) return false;
      if (filters.presence && r.presence !== filters.presence) return false;
      if (filters.q) {
        var q = String(filters.q).toLowerCase();
        var hay = (r.name + ' ' + r.location + ' ' + r.building + ' ' + r.room).toLowerCase();
        if (hay.indexOf(q) < 0) return false;
      }
      return true;
    }).sort(function (a, b) {
      var order = (RISK[a.risk_level] ? RISK[a.risk_level].order : 9) - (RISK[b.risk_level] ? RISK[b.risk_level].order : 9);
      if (order !== 0) return order;
      return (b.inactive_days || 0) - (a.inactive_days || 0);
    });
    var counts = { normal: 0, watch: 0, warning: 0, critical: 0 };
    all.forEach(function (r) { counts[r.risk_level] += 1; });
    return {
      items: items.slice(0, 50),
      total: items.length,
      limit: 50,
      offset: 0,
      risk_counts: counts
    };
  }

  /* ======================================================================
     六、渲染：KPI / 图表 / 预警 / 住户表格
     ====================================================================== */
  function setKpi(id, value, decimals) {
    var el = document.getElementById(id);
    if (el) countUp(el, value, decimals);
  }

  function renderKpis(overview) {
    var s = overview.stats || {};
    var home = num(s.home);
    var away = num(s.away);
    var residents = num(s.residents);
    var homeRate = (residents && home != null) ? Math.round(home / residents * 1000) / 10 : null;
    var openAlerts = num(s.open_alerts);
    var critical = num(s.critical_alerts);
    var inactive3 = num(s.inactive_3d);
    var inactive7 = num(s.inactive_7d);

    setKpi('cKpiResidents', residents, 0);
    setKpi('cKpiHome', home, 0);
    setKpi('cKpiInactive3', inactive3, 0);
    setKpi('cKpiOpenAlerts', openAlerts, 0);
    setKpi('cKpiCritical', critical, 0);
    setKpi('cKpiToday', num(s.today_access_events), 0);

    var foots = {
      cKpiResidentsFoot: '<span class="trend flat">外出 ' + esc(fmtNum(away)) + ' 户</span><span class="trend-divider"></span><span>含长期外出</span>',
      cKpiHomeFoot: '<span class="trend up">' + icon('home') + '在住率 ' + esc(fmtFixed(homeRate, 1)) + '%</span>',
      cKpiInactive3Foot: '<span class="trend down">' + icon('clock') + '超 7 天 ' + esc(fmtNum(inactive7)) + ' 户</span><span class="trend-divider"></span><span>口径：连续未出门</span>',
      cKpiOpenAlertsFoot: '<span class="trend flat">待跟进 · 含 L1/L2/L3</span>',
      cKpiCriticalFoot: '<span class="trend down">' + icon('alert') + 'L3 建议 30 分钟内上门</span>',
      cKpiTodayFoot: '<span class="trend flat">门禁进出合计 · 含物业代登记</span>'
    };
    Object.keys(foots).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.innerHTML = foots[id];
    });
  }

  function chartBaseTooltip(extra) {
    var base = {
      backgroundColor: 'rgba(11,31,58,0.94)',
      borderWidth: 0,
      padding: [9, 12],
      textStyle: { color: '#e6f0fb', fontSize: 11.5 },
      extraCssText: 'border-radius:9px;box-shadow:0 14px 30px -16px rgba(0,0,0,.55);'
    };
    Object.keys(extra || {}).forEach(function (k) { base[k] = extra[k]; });
    return base;
  }

  function renderRiskChart(list) {
    var el = $('#chartCommunityRisk');
    if (!window.Charts || !el) return;
    var data = arr(list).filter(function (d) { return (num(d.value) || 0) > 0; }).map(function (d, i) {
      return {
        name: d.name || '未分类',
        value: num(d.value) || 0,
        itemStyle: { color: d.color || ['#16a34a', '#f59e0b', '#f97316', '#ef4444'][i % 4] }
      };
    });
    if (!data.length) return Charts.empty(el, '暂无风险分布', '接口未返回 risk_distribution');
    var inst = Charts.init(el);
    if (!inst) return;
    var total = data.reduce(function (sum, d) { return sum + d.value; }, 0);
    inst.setOption({
      animationDuration: 650,
      tooltip: chartBaseTooltip({ trigger: 'item', formatter: '{b}<br/><b>{c}</b> 户 · {d}%' }),
      legend: {
        bottom: 0, left: 'center', icon: 'circle', itemWidth: 8, itemHeight: 8, itemGap: 12,
        textStyle: { color: '#4b5f7a', fontSize: 10.5 }
      },
      title: {
        text: fmtNum(total), subtext: '住户总数', left: '50%', top: '32%', textAlign: 'center',
        textStyle: { fontSize: 22, fontWeight: 700, color: '#0f2544' },
        subtextStyle: { fontSize: 9.5, color: '#8496ad' }
      },
      series: [{
        name: '风险分布', type: 'pie', radius: ['46%', '65%'], center: ['50%', '44%'],
        itemStyle: { borderColor: '#fff', borderWidth: 2, borderRadius: 4 },
        label: { show: false }, labelLine: { show: false },
        emphasis: { scale: true, scaleSize: 4, itemStyle: { shadowBlur: 12, shadowColor: 'rgba(15,37,68,.18)' } },
        data: data
      }]
    }, true);
  }

  function renderTrendChart(list) {
    var el = $('#chartCommunityTrend');
    if (!window.Charts || !el) return;
    list = arr(list);
    if (!list.length) return Charts.empty(el, '暂无趋势数据', '接口未返回近 7 日门禁/预警趋势');
    var inst = Charts.init(el);
    if (!inst) return;
    var labels = list.map(function (d) { return String(d.date || '').slice(5); });
    var access = list.map(function (d) { return num(d.access) || 0; });
    var alerts = list.map(function (d) { return num(d.alerts) || 0; });
    inst.setOption({
      animationDuration: 650,
      tooltip: chartBaseTooltip({ trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(37,99,235,.05)' } } }),
      legend: {
        top: 0, right: 0, icon: 'roundRect', itemWidth: 9, itemHeight: 9, itemGap: 14,
        textStyle: { color: '#4b5f7a', fontSize: 10.5 }
      },
      grid: { left: 6, right: 12, top: 34, bottom: 0, containLabel: true },
      xAxis: {
        type: 'category', data: labels, boundaryGap: true,
        axisLine: { lineStyle: { color: '#e3ebf5' } }, axisTick: { show: false },
        axisLabel: { color: '#8496ad', fontSize: 10 }
      },
      yAxis: [
        {
          type: 'value', minInterval: 1, name: '门禁事件',
          nameTextStyle: { color: '#8496ad', fontSize: 9.5 },
          axisLine: { show: false }, axisTick: { show: false },
          axisLabel: { color: '#8496ad', fontSize: 10 },
          splitLine: { lineStyle: { color: '#e8eef7', type: 'dashed' } }
        },
        {
          type: 'value', minInterval: 1, name: '预警',
          nameTextStyle: { color: '#8496ad', fontSize: 9.5 },
          axisLine: { show: false }, axisTick: { show: false },
          axisLabel: { color: '#8496ad', fontSize: 10 },
          splitLine: { show: false }
        }
      ],
      series: [
        {
          name: '门禁事件', type: 'bar', barMaxWidth: 20, barWidth: '44%',
          itemStyle: { borderRadius: [4, 4, 0, 0], color: '#2563eb' },
          data: access
        },
        {
          name: '新增预警', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'circle', symbolSize: 6,
          lineStyle: { width: 2.4, color: '#f97316' },
          itemStyle: { color: '#f97316', borderWidth: 2, borderColor: '#fff' },
          areaStyle: {
            color: window.echarts ? new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(249,115,22,.18)' },
              { offset: 1, color: 'rgba(249,115,22,0)' }
            ]) : 'rgba(249,115,22,.12)'
          },
          data: alerts
        }
      ]
    }, true);
  }

  function renderBuildingChart(list) {
    var el = $('#chartCommunityBuildings');
    if (!window.Charts || !el) return;
    list = arr(list).filter(function (d) { return d && (d.building || d.name); });
    if (!list.length) return Charts.empty(el, '暂无楼栋数据', '接口未返回 building_ranking');
    var sorted = list.slice().sort(function (a, b) { return (num(b.inactive) || 0) - (num(a.inactive) || 0); }).slice(0, 8);
    var inst = Charts.init(el);
    if (!inst) return;
    inst.setOption({
      animationDuration: 650,
      tooltip: chartBaseTooltip({
        trigger: 'axis',
        axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(37,99,235,.05)' } },
        formatter: function (params) {
          if (!params || !params.length) return '';
          var item = sorted[params[0].dataIndex] || {};
          return esc(item.building || item.name || '') + '<br/>未出门住户：<b>' + (item.inactive || 0) + '</b> 户' +
            (item.residents != null ? '<br/>楼栋住户：<b>' + item.residents + '</b> 户' : '');
        }
      }),
      grid: { left: 4, right: 42, top: 6, bottom: 0, containLabel: true },
      xAxis: { type: 'value', show: false, splitLine: { show: false } },
      yAxis: {
        type: 'category', inverse: true,
        data: sorted.map(function (d) { return d.building || d.name || '—'; }),
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: '#4b5f7a', fontSize: 10.5, margin: 8 }
      },
      series: [{
        type: 'bar', barWidth: 11, showBackground: true,
        backgroundStyle: { color: '#f1f6fd', borderRadius: 6 },
        itemStyle: { borderRadius: 6, color: '#f97316' },
        label: { show: true, position: 'right', color: '#8496ad', fontSize: 10, formatter: '{c} 户' },
        data: sorted.map(function (d) { return num(d.inactive) || 0; })
      }]
    }, true);
  }

  function sortAlerts(list) {
    return list.slice().sort(function (a, b) {
      var ra = LEVEL_META[a.level] ? LEVEL_META[a.level].rank : 9;
      var rb = LEVEL_META[b.level] ? LEVEL_META[b.level].rank : 9;
      if (ra !== rb) return ra - rb;
      return String(b.created_at || '').localeCompare(String(a.created_at || ''));
    });
  }

  function alertTagsHtml(tags) {
    return arr(tags).map(function (t) {
      return '<span class="tag-pill">' + esc(t) + '</span>';
    }).join('');
  }

  function renderAlerts() {
    var wrap = $('#communityAlertList');
    var countEl = $('#communityAlertCount');
    if (!wrap) return;
    var list = sortAlerts(state.alerts.items);
    if (countEl) countEl.textContent = '待处理 ' + fmtNum(list.length) + ' 条';

    if (!list.length) {
      var desc = state.alerts.live
        ? '当前没有待处理预警，可点击「立即巡检」重新扫描住户出入情况。'
        : '预警接口未就绪或暂无数据，请启动后端服务后重试。';
      wrap.innerHTML = (window.UI && UI.emptyHTML)
        ? UI.emptyHTML(state.alerts.live ? '暂无待处理预警' : '服务未就绪', desc, 'shield',
          state.alerts.live ? '' : '<button class="btn ghost sm" type="button" data-retry-page="community">重试接口</button>')
        : '<div class="recent-empty">' + esc(desc) + '</div>';
      return;
    }

    wrap.innerHTML = list.map(function (a) {
      var cls = LEVEL_CLASS[a.level] || 'l1';
      var acking = !!state.acking[a.id];
      return '<article class="alert-item ' + cls + '">' +
        '<div class="alert-side"><span class="alert-level ' + cls + '">' + esc(a.level) + '</span>' +
        '<span class="alert-level-text">' + esc(a.level_label) + '</span></div>' +
        '<div class="alert-main">' +
        '<div class="alert-title"><strong>' + esc(a.resident_name) + '</strong>' +
        alertTagsHtml(a.tags) +
        '<span class="alert-time">' + icon('clock') + esc(shortTime(a.created_at)) + '</span></div>' +
        '<p class="alert-msg">' + esc(a.message) + '</p>' +
        '<div class="alert-meta">' +
        '<span>' + icon('pin') + esc(a.location) + '</span>' +
        '<span>未出门 <b>' + esc(fmtFixed(a.inactive_days, 1)) + '</b> 天</span>' +
        '<span>阈值 ' + esc(fmtFixed(a.threshold_days, 1)) + ' 天</span>' +
        '<span class="alert-status">' + esc(a.status_label || '待处理') + '</span>' +
        '</div></div>' +
        '<div class="alert-action"><button class="btn ghost sm" type="button" data-ack-alert="' + esc(a.id) + '"' +
        (acking ? ' disabled' : '') + '>' + (acking ? '处理中…' : '确认处理') + '</button></div>' +
        '</article>';
    }).join('');
  }

  function residentRow(r) {
    var risk = RISK[r.risk_level] || RISK.normal;
    var presenceCls = r.presence === 'home' ? 'home' : 'away';
    var tags = arr(r.tags).slice(0, 2).map(function (t) {
      return '<span class="tag-pill">' + esc(t) + '</span>';
    }).join('');
    var inactive = num(r.inactive_days);
    var dayCls = r.risk_level === 'critical' ? 'critical' : r.risk_level === 'warning' ? 'warning' : r.risk_level === 'watch' ? 'watch' : '';
    var isLeaving = !!state.leaving[r.id];
    return '<tr data-resident="' + esc(r.id) + '">' +
      '<td><div class="resident-name"><strong>' + esc(r.name) + '</strong>' +
      '<span>' + (r.age != null ? esc(r.age) + '岁' : '年龄未知') + (r.gender ? ' · ' + esc(r.gender) : '') + '</span></div>' +
      (tags ? '<div class="resident-tags">' + tags + '</div>' : '') + '</td>' +
      '<td><span class="resident-loc">' + icon('pin') + esc(r.location) + '</span></td>' +
      '<td><span class="presence-pill ' + presenceCls + '">' + esc(r.presence_label) + '</span></td>' +
      '<td class="num"><span class="resident-days ' + dayCls + '">' +
      (inactive == null ? '—' : esc(fmtFixed(inactive, 1))) + '<em>天</em></span>' +
      '<small class="resident-threshold">' + (r.presence === 'away' ? '已外出' : '未出门') +
      ' · 阈值 ' + esc(fmtFixed(r.threshold_days, 1)) + ' 天</small></td>' +
      '<td class="cell-time">' + esc(shortTime(r.last_exit_at)) + '</td>' +
      '<td><span class="risk-pill ' + esc(r.risk_level) + '">' + esc(r.risk_label) + '</span></td>' +
      '<td><div class="care-cell">' + carePill(r.care_level) +
      (r.persona_label ? '<small title="' + esc(r.persona_label) + '">' + esc(r.persona_label) + '</small>' : '') +
      '</div></td>' +
      '<td class="num"><div class="row-actions">' +
      '<button class="btn ghost sm" type="button" data-exit-resident="' + esc(r.id) + '"' + (isLeaving ? ' disabled' : '') + '>' +
      (isLeaving ? '登记中…' : '登记外出') + '</button>' +
      '<button class="link-btn" type="button" data-detail-resident="' + esc(r.id) + '">详情</button>' +
      '</div></td>' +
      '</tr>';
  }

  function renderResidents() {
    var tbody = $('#communityResidentRows');
    var metaEl = $('#communityResidentMeta');
    var tableMeta = $('#communityTableMeta');
    if (!tbody) return;
    var list = state.residents.items;
    var filters = state.residents.filters;
    var filtered = !!(filters.risk || filters.presence || filters.q);

    if (metaEl) metaEl.textContent = '共 ' + fmtNum(state.residents.total) + ' 户';
    if (tableMeta) {
      tableMeta.textContent = filtered
        ? '筛选结果 ' + fmtNum(list.length) + ' 户 / 共 ' + fmtNum(state.residents.total) + ' 户'
        : '共 ' + fmtNum(state.residents.total) + ' 户 · 显示前 ' + fmtNum(list.length) + ' 户';
    }

    if (!list.length) {
      var title = state.residents.live ? '暂无住户数据' : '服务未就绪';
      var desc = state.residents.live
        ? '当前筛选条件下没有住户，试试调整风险等级或关键词。'
        : '住户接口未就绪，无法加载住户档案，请启动后端服务后重试。';
      tbody.innerHTML = '<tr><td colspan="8">' +
        (window.UI && UI.emptyHTML
          ? UI.emptyHTML(title, desc, 'user', state.residents.live ? '' : '<button class="btn ghost sm" type="button" data-retry-page="community">重试接口</button>')
          : '<div class="recent-empty">' + esc(desc) + '</div>') +
        '</td></tr>';
      return;
    }
    tbody.innerHTML = list.map(residentRow).join('');
  }

  function resizeCharts() {
    var pageEl = document.getElementById('page-community');
    if (pageEl && window.Charts) Charts.resizeIn(pageEl);
  }

  function renderAll() {
    if (!state.overview) return;
    renderKpis(state.overview);
    renderRiskChart(state.overview.risk_distribution);
    renderTrendChart(state.overview.trend);
    renderBuildingChart(state.overview.building_ranking);
    renderAlerts();
    renderResidents();
    requestAnimationFrame(resizeCharts);
  }

  /* ======================================================================
     七、示例数据提示
     ====================================================================== */
  function sampleState() {
    return {
      sample: !state.overviewLive || !state.residents.live || !state.alerts.live,
      error: state.error
    };
  }

  function renderSampleNote() {
    var el = $('#communitySampleNote');
    if (!el) return;
    var st = sampleState();
    el.hidden = !st.sample;
    if (!st.sample) { el.innerHTML = ''; return; }
    var errText = st.error && window.API ? API.describeError(st.error) : '';
    el.innerHTML = icon('alert') +
      '<div><strong>服务未就绪 · 示例数据。</strong>' +
      esc(errText ? errText + '，当前展示本地示例住户与预警数据；接口恢复后自动切换真实数据。'
        : '小区守护接口暂不可用，当前展示本地示例数据；接口恢复后自动切换真实数据。') +
      '</div>' +
      '<div class="sample-note-actions"><button class="btn ghost sm" type="button" data-retry-page="community">重试接口</button></div>';
  }

  /* ======================================================================
     八、加载流程
     ====================================================================== */
  function setLoading(loading) {
    state.loading = loading;
    var refresh = $('#communityRefresh');
    if (refresh) refresh.classList.toggle('loading', loading);
    var scan = $('#communityScan');
    if (scan) scan.disabled = loading;
  }

  async function loadAll(force) {
    var seq = ++state.seq;
    setLoading(true);
    var firstLoad = !state.loaded;
    if (firstLoad) {
      state.residents.live = false;
      state.overviewLive = false;
      state.alerts.live = false;
    }

    var results = await Promise.all([fetchOverview(), fetchResidents(), fetchAlerts()]);
    if (seq !== state.seq) return;

    var overviewRes = results[0], residentRes = results[1], alertRes = results[2];
    state.overview = overviewRes.data;
    state.overviewLive = overviewRes.live;
    state.residents.items = residentRes.data.items;
    state.residents.total = residentRes.data.total;
    state.residents.risk_counts = residentRes.data.risk_counts || {};
    state.residents.live = residentRes.live;
    state.alerts.items = alertRes.data.items;
    state.alerts.live = alertRes.live;

    var errors = [overviewRes, residentRes, alertRes].filter(function (r) { return r.error; });
    state.error = errors.length ? errors[0].error : null;
    if (overviewRes.sample && alertRes.sample && state.overview && state.overview.alerts && !state.alerts.items.length) {
      state.alerts.items = sortAlerts(arr(state.overview.alerts).map(normalizeAlert));
    }

    state.loaded = true;
    setLoading(false);
    renderSampleNote();
    renderAll();

    if (overviewRes.live) setApiStatus('ok', '服务正常');
    else if (state.error) setApiStatus(state.error.kind === 'http' ? 'off' : 'down', state.error.kind === 'http' ? '接口降级' : '服务未连接');
  }

  async function refreshOverviewAlerts(silent) {
    if (state.loading) return;
    var seq = state.seq;
    try {
      var results = await Promise.all([
        API.communityOverview(),
        API.communityAlerts({ status: 'open', limit: 100 })
      ]);
      if (seq !== state.seq) return;
      var overviewRaw = results[0], alertsRaw = results[1];
      if (overviewRaw && typeof overviewRaw === 'object') {
        state.overview = normalizeOverview(overviewRaw);
        state.overviewLive = true;
        renderKpis(state.overview);
        renderRiskChart(state.overview.risk_distribution);
        renderTrendChart(state.overview.trend);
        renderBuildingChart(state.overview.building_ranking);
      }
      if (alertsRaw && typeof alertsRaw === 'object') {
        state.alerts.items = normalizeAlertPage(alertsRaw).items;
        state.alerts.live = true;
        renderAlerts();
      }
      renderSampleNote();
      requestAnimationFrame(resizeCharts);
    } catch (e) {
      if (!silent) return;
      /* 静默刷新失败：保留现有数据，不打扰用户 */
    }
  }

  /* ======================================================================
     九、操作：巡检 / 确认预警 / 登记外出
     ====================================================================== */
  async function runScan() {
    if (state.loading) return;
    if (!state.overviewLive) {
      toast('当前为示例数据，立即巡检需要后端服务可用', 'warn');
      return;
    }
    var btn = $('#communityScan');
    if (btn) { btn.disabled = true; btn.classList.add('loading'); }
    try {
      var res = await API.communityScan({});
      var scanned = num(res && res.scanned) || 0;
      var created = num(res && res.alerts_created) || 0;
      toast('巡检完成：扫描 ' + fmtNum(scanned) + ' 户，新生成 ' + fmtNum(created) + ' 条预警', created > 0 ? 'success' : 'info');
      await loadAll(true);
    } catch (e) {
      toast('巡检失败：' + (window.API ? API.describeError(e) : '接口未就绪'), 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.classList.remove('loading'); }
    }
  }

  async function ackAlert(alertId, btn) {
    if (state.acking[alertId]) return;
    if (!state.alerts.live) {
      toast('当前为示例数据，确认预警需要后端服务可用', 'warn');
      return;
    }
    state.acking[alertId] = true;
    if (btn) { btn.disabled = true; btn.textContent = '处理中…'; }
    try {
      await API.communityAckAlert(alertId, { operator: '物业值班员' });
      delete state.acking[alertId];
      toast('预警已确认处理', 'success');
      await loadAll(true);
    } catch (e) {
      toast('确认失败：' + (window.API ? API.describeError(e) : '接口未就绪'), 'error');
    } finally {
      delete state.acking[alertId];
      if (btn && btn.parentNode) { btn.disabled = false; btn.textContent = '确认处理'; }
    }
  }

  async function registerExit(residentId, btn) {
    if (!residentId || state.leaving[residentId]) return;
    if (!state.residents.live) {
      toast('当前为示例数据，登记外出需要后端服务可用', 'warn');
      return;
    }
    state.leaving[residentId] = true;
    if (btn) { btn.disabled = true; btn.textContent = '登记中…'; }
    try {
      var res = await API.communityAccess({ resident_id: residentId, event_type: 'exit', source: '物业代登记' });
      delete state.leaving[residentId];
      var name = (res && res.resident && res.resident.name) || '该住户';
      var resolved = num(res && res.alerts_resolved);
      toast('已登记 ' + name + ' 外出' + (resolved ? '，自动解除 ' + resolved + ' 条预警' : ''), 'success');
      await loadAll(true);
      if (state.drawer.id === residentId) closeResidentDrawer();
    } catch (e) {
      toast('登记外出失败：' + (window.API ? API.describeError(e) : '接口未就绪'), 'error');
    } finally {
      delete state.leaving[residentId];
      if (btn && btn.parentNode) { btn.disabled = false; btn.textContent = '登记外出'; }
    }
  }

  /* ======================================================================
     十、住户详情抽屉
     ====================================================================== */
  function setDrawerEditEnabled(enabled) {
    var btn = $('#residentDrawerEdit');
    if (btn) btn.disabled = !enabled;
  }

  function openResidentDrawerUI() {
    var drawer = $('#residentDrawer');
    var overlay = $('#residentDrawerOverlay');
    if (!drawer || !overlay) return;
    drawer.classList.add('open');
    drawer.setAttribute('aria-hidden', 'false');
    overlay.classList.add('open');
    document.body.classList.add('drawer-open');
    setDrawerEditEnabled(false);
    var body = $('#residentDrawerBody');
    if (body) body.innerHTML = '<div class="drawer-loading"><span class="spinner"></span><span>正在加载住户档案与出入记录…</span></div>';
  }

  function closeResidentDrawer() {
    var drawer = $('#residentDrawer');
    var overlay = $('#residentDrawerOverlay');
    if (!drawer || !overlay) return;
    drawer.classList.remove('open');
    drawer.setAttribute('aria-hidden', 'true');
    overlay.classList.remove('open');
    document.body.classList.remove('drawer-open');
    setDrawerEditEnabled(false);
    state.drawer.id = null;
    state.drawer.detail = null;
  }

  function eventItem(ev) {
    var isExit = ev.event_type === 'exit';
    return '<div class="event-item ' + (isExit ? 'exit' : 'entry') + '">' +
      '<span class="event-dot"></span>' +
      '<div class="event-main"><strong>' + esc(ev.event_label || (isExit ? '出门' : '进门')) + '</strong>' +
      '<span class="event-source">' + esc(ev.source || '门禁记录') + '</span></div>' +
      '<span class="event-time">' + esc(fullTime(ev.occurred_at)) + '</span>' +
      '</div>';
  }

  function residentAlertItem(a) {
    var cls = LEVEL_CLASS[a.level] || 'l1';
    return '<div class="resident-alert-item ' + cls + '">' +
      '<div class="resident-alert-head"><span class="alert-level ' + cls + '">' + esc(a.level) + '</span>' +
      '<strong>' + esc(a.level_label || '') + '</strong>' +
      '<span class="resident-alert-status">' + esc(a.status_label || '待处理') + '</span>' +
      '<span class="resident-alert-time">' + esc(shortTime(a.created_at)) + '</span></div>' +
      '<p>' + esc(a.message || '') + '</p>' +
      (a.ack_at ? '<small>确认：' + esc(fullTime(a.ack_at)) + (a.ack_by ? ' · ' + esc(a.ack_by) : '') + '</small>' : '') +
      '</div>';
  }

  function renderProfileSection(resident) {
    var p = normalizeProfile(resident.profile);
    var persona = p.persona.length ? p.persona : (resident.persona_label ? resident.persona_label.split(' / ') : []);
    var level = resident.care_level || p.care_level || '自动评估';
    var completeness = num(p.completeness);
    var riskChips = p.risk_factors.length
      ? p.risk_factors.map(function (t) { return '<span class="risk-chip">' + esc(t) + '</span>'; }).join('')
      : '<span class="risk-chip none">暂无显著风险</span>';
    var actions = p.recommended_actions.length
      ? p.recommended_actions.map(function (a, i) {
        return '<li><span class="profile-action-index">' + pad2(i + 1) + '</span><span>' + esc(a) + '</span></li>';
      }).join('')
      : '<li><span class="profile-action-index">—</span><span>暂无建议动作</span></li>';
    var services = p.service_needs.length
      ? p.service_needs.map(function (t) { return '<span class="tag-pill">' + esc(t) + '</span>'; }).join('')
      : '<span class="profile-meta-empty">未填写</span>';
    var pct = completeness == null ? null : Math.max(0, Math.min(100, completeness));
    return '<div class="detail-section">' +
      '<div class="detail-section-head">' + icon('robot') + '<h4>住户画像</h4>' +
      '<span class="count">画像完整度 ' + (pct == null ? '—' : esc(fmtNum(pct)) + '%') + '</span></div>' +
      '<div class="detail-card profile-card">' +
      '<div class="profile-head">' +
      '<div class="profile-persona">' +
      (persona.length
        ? persona.map(function (t) { return '<span class="persona-chip">' + esc(t) + '</span>'; }).join('')
        : '<span class="persona-chip">普通住户</span>') +
      '</div>' +
      '<div class="profile-care">' + carePill(level) + '</div>' +
      '</div>' +
      '<p class="profile-summary">' + esc(p.summary ||
        (resident.name + '，' + (resident.age != null ? resident.age + ' 岁' : '年龄未知') + '。')) + '</p>' +
      '<div class="profile-completeness">' +
      '<div class="profile-completeness-head"><span>档案完整度</span><strong>' +
      (pct == null ? '—' : esc(fmtNum(pct)) + '%') + '</strong></div>' +
      '<div class="progress"><i style="width:' + (pct == null ? 0 : pct) + '%"></i></div>' +
      '</div>' +
      '<div class="profile-block"><h5>风险因素</h5><div class="risk-chips">' + riskChips + '</div></div>' +
      '<div class="profile-block"><h5>建议动作</h5><ol class="profile-actions">' + actions + '</ol></div>' +
      '<div class="profile-meta">' +
      '<span>居住状态：<b>' + esc(p.living_status || '—') + '</b></span>' +
      '<span>健康状况：<b>' + esc(p.health_status || '—') + '</b></span>' +
      '<span>行动能力：<b>' + esc(p.mobility || '—') + '</b></span>' +
      (p.habits ? '<span>生活习惯：<b>' + esc(p.habits) + '</b></span>' : '') +
      (p.notes ? '<span>备注：<b>' + esc(p.notes) + '</b></span>' : '') +
      '<span>紧急联系人：<b>' + esc(p.emergency_contact || resident.emergency_contact || '—') + '</b></span>' +
      '<span>更新时间：<b>' + esc(p.updated_at ? fullTime(p.updated_at) : '—') + '</b></span>' +
      '</div>' +
      '<div class="profile-meta"><span class="profile-services-label">服务需求：</span><span class="profile-services">' + services + '</span></div>' +
      '</div></div>';
  }

  function renderResidentDetail(detail, sample, err) {
    var resident = detail.resident;
    var body = $('#residentDrawerBody');
    var title = $('#residentDrawerTitle');
    var badges = $('#residentDrawerBadges');
    if (!body) return;
    setDrawerEditEnabled(!sample);
    if (title) title.textContent = resident.name + ' · ' + (resident.location || '');
    if (badges) {
      badges.innerHTML = '<span class="risk-pill ' + esc(resident.risk_level) + '">' + esc(resident.risk_label) + '</span>' +
        '<span class="presence-pill ' + (resident.presence === 'home' ? 'home' : 'away') + '">' + esc(resident.presence_label) + '</span>' +
        carePill(resident.care_level) +
        arr(resident.tags).map(function (t) { return '<span class="badge-soft">' + esc(t) + '</span>'; }).join('') +
        (sample ? '<span class="badge-soft amber">示例数据</span>' : '');
    }

    var items = [
      ['住户编号', resident.id, true],
      ['年龄 / 性别', (resident.age != null ? resident.age + ' 岁' : '—') + (resident.gender ? ' · ' + resident.gender : '')],
      ['联系电话', resident.phone || '—'],
      ['家庭住址', resident.location],
      ['家属联系人', resident.family_contact || '—'],
      ['紧急联系人', resident.emergency_contact || '—'],
      ['关怀等级', resident.care_level || '自动评估'],
      ['画像标签', resident.persona_label || '—'],
      ['在家状态', resident.presence_label],
      ['连续未出门', fmtFixed(resident.inactive_days, 1) + ' 天'],
      ['预警阈值', fmtFixed(resident.threshold_days, 1) + ' 天'],
      ['最近出门', fullTime(resident.last_exit_at)],
      ['最近进门', fullTime(resident.last_entry_at)],
      ['外出原因', resident.absence_reason || '—'],
      ['预计返回', resident.away_until ? fullTime(resident.away_until) : '—']
    ];

    var html = '';
    if (sample) {
      html += '<div class="sample-note" style="margin-bottom:14px">' + icon('alert') +
        '<div><strong>示例数据。</strong>' + esc(err && window.API ? API.describeError(err) : '住户详情接口未就绪') +
        '，以下为演示占位；登记外出需要后端服务可用。</div></div>';
    }
    html += '<div class="detail-section">' +
      '<div class="detail-section-head">' + icon('user') + '<h4>住户信息</h4>' +
      '<span class="count">' + esc(resident.risk_label) + ' · 阈值 ' + esc(fmtFixed(resident.threshold_days, 0)) + ' 天</span></div>' +
      '<div class="detail-card"><div class="detail-grid">' + items.map(function (row) {
        return '<div class="detail-item"><span>' + esc(row[0]) + '</span><strong' + (row[2] ? ' class="mono"' : '') + '>' + esc(row[1]) + '</strong></div>';
      }).join('') + '</div>' +
      (resident.open_alert ? '<div class="resident-open-alert">' + icon('alert') +
        '<div><strong>当前未处理预警（' + esc(resident.open_alert.level || '') + '）</strong>' +
        esc(resident.open_alert.message || '') + '</div></div>' : '') +
      '</div></div>';

    html += renderProfileSection(resident);

    var events = arr(detail.events);
    html += '<div class="detail-section">' +
      '<div class="detail-section-head">' + icon('walk') + '<h4>最近出入记录</h4>' +
      '<span class="count">' + (events.length ? '最近 ' + events.length + ' 条' : '暂无记录') + '</span></div>' +
      '<div class="detail-card"><div class="event-list">' +
      (events.length ? events.slice(0, 30).map(eventItem).join('')
        : '<div class="recent-empty">暂无门禁出入记录</div>') +
      '</div></div></div>';

    var alerts = arr(detail.alerts);
    html += '<div class="detail-section">' +
      '<div class="detail-section-head">' + icon('shield') + '<h4>历史预警</h4>' +
      '<span class="count">' + (alerts.length ? alerts.length + ' 条' : '暂无预警') + '</span></div>' +
      '<div class="detail-card">' +
      (alerts.length ? alerts.map(residentAlertItem).join('') : '<div class="recent-empty">该住户暂无历史预警</div>') +
      '</div></div>';

    html += '<div class="detail-section">' +
      '<div class="action-panel"><h4>' + icon('walk') + '关怀操作</h4>' +
      '<p>物业可代住户登记外出，登记后将自动解除未出门预警并刷新风险等级。</p>' +
      '<button class="btn primary" type="button" data-drawer-exit="' + esc(resident.id) + '">' + icon('walk') + '登记外出</button>' +
      '</div></div>';

    body.innerHTML = html;
  }

  function renderDrawerError(residentId, err) {
    setDrawerEditEnabled(false);
    var body = $('#residentDrawerBody');
    if (!body) return;
    body.innerHTML = '<div class="drawer-loading" style="padding-top:50px">' +
      (window.UI && UI.emptyHTML
        ? UI.emptyHTML('无法加载住户 ' + residentId,
          (err && window.API ? API.describeError(err) : '住户详情接口未就绪') + '，请确认后端服务已启动后重试。',
          'user', '<button class="btn primary sm" type="button" data-retry-resident="' + esc(residentId) + '">重新加载</button>')
        : '<div class="recent-empty">无法加载住户详情</div>') +
      '</div>';
  }

  async function openResident(residentId) {
    if (!residentId) return;
    var seq = ++state.drawer.seq;
    state.drawer.id = residentId;
    state.drawer.detail = null;
    openResidentDrawerUI();
    var title = $('#residentDrawerTitle');
    if (title) title.textContent = residentId;
    var data = null, sample = false, err = null;
    try {
      var raw = await API.communityResident(residentId);
      if (!raw || !raw.resident) throw new Error('住户不存在');
      data = { resident: normalizeResident(raw.resident), events: arr(raw.events), alerts: arr(raw.alerts).map(normalizeAlert) };
    } catch (e) {
      err = e;
      sample = true;
      var mockDetail = getMock().residentDetail(residentId);
      if (mockDetail) {
        data = {
          resident: normalizeResident(mockDetail.resident),
          events: mockDetail.events,
          alerts: mockDetail.alerts.map(normalizeAlert)
        };
      }
    }
    if (seq !== state.drawer.seq || state.drawer.id !== residentId) return;
    if (!data) {
      state.drawer.error = err;
      renderDrawerError(residentId, err);
      return;
    }
    state.drawer.detail = data;
    state.drawer.sample = sample;
    state.drawer.error = err;
    renderResidentDetail(data, sample, err);
  }

  /* ======================================================================
     十一、住户档案表单（新增 / 编辑画像）
     ====================================================================== */
  function setFormError(text) {
    var el = $('#residentFormError');
    if (!el) return;
    el.hidden = !text;
    el.textContent = text || '';
  }

  function formValue(id) {
    var el = document.getElementById(id);
    return el ? String(el.value == null ? '' : el.value).trim() : '';
  }

  function setFormValue(id, value) {
    var el = document.getElementById(id);
    if (el) el.value = value == null ? '' : String(value);
  }

  function renderChoices(container, selected, all) {
    if (!container) return;
    var values = all.slice();
    arr(selected).forEach(function (v) {
      if (v && values.indexOf(v) < 0) values.push(String(v));
    });
    var chosen = arr(selected).map(String);
    container.innerHTML = values.map(function (v) {
      var extra = all.indexOf(v) < 0;
      return '<label class="tag-choice' + (extra ? ' extra' : '') + '">' +
        '<input type="checkbox" value="' + esc(v) + '"' + (chosen.indexOf(String(v)) >= 0 ? ' checked' : '') + '>' +
        '<span>' + esc(v) + '</span></label>';
    }).join('');
  }

  function checkedValues(container) {
    if (!container) return [];
    return $$('input[type="checkbox"]:checked', container).map(function (input) { return input.value; });
  }

  function openResidentForm(resident) {
    var overlay = $('#residentFormOverlay');
    if (!overlay) return;
    var edit = !!(resident && resident.id);
    state.form.open = true;
    state.form.mode = edit ? 'edit' : 'create';
    state.form.residentId = edit ? resident.id : null;
    state.form.source = resident || null;
    state.form.submitting = false;
    setFormError('');

    var title = $('#residentFormTitle');
    var sub = $('#residentFormSub');
    var submitText = $('#residentFormSubmitText');
    if (title) title.textContent = edit ? '编辑住户画像' : '新增住户';
    if (sub) {
      sub.textContent = edit
        ? ('正在编辑 ' + (resident.name || '') + '（' + resident.id + '）的档案，保存后自动重新评估关怀等级与画像')
        : '填写基础档案与关怀信息，保存后由住户画像 Agent 自动生成画像与关怀等级';
    }
    if (submitText) submitText.textContent = edit ? '保存修改' : '保存住户';

    var profile = normalizeProfile((resident && resident.profile) || {});
    setFormValue('rfName', edit ? resident.name : '');
    setFormValue('rfGender', edit ? (resident.gender || '未知') : '未知');
    setFormValue('rfAge', edit ? (resident.age != null ? resident.age : '') : 60);
    setFormValue('rfPhone', edit ? (resident.phone === '—' ? '' : resident.phone) : '');
    setFormValue('rfBuilding', edit ? resident.building : '');
    setFormValue('rfUnit', edit ? resident.unit : '');
    setFormValue('rfRoom', edit ? resident.room : '');
    setFormValue('rfLiving', edit ? profile.living_status : '');
    setFormValue('rfHealth', edit ? profile.health_status : '');
    setFormValue('rfMobility', edit ? profile.mobility : '');
    setFormValue('rfCareLevel', edit ? (resident.care_level || profile.care_level || '') : '');
    setFormValue('rfFamily', edit
      ? (profile.family_members || (resident.family_contact && resident.family_contact !== '—' ? resident.family_contact : ''))
      : '');
    setFormValue('rfEmergency', edit ? (resident.emergency_contact || profile.emergency_contact || '') : '');
    setFormValue('rfHabits', edit ? profile.habits : '');
    setFormValue('rfNotes', edit ? profile.notes : '');

    renderChoices($('#rfTagChoices'), edit ? resident.tags : [], FORM_TAGS);
    renderChoices($('#rfServiceChoices'), edit ? profile.service_needs : [], FORM_SERVICES);

    overlay.hidden = false;
    requestAnimationFrame(function () { overlay.classList.add('open'); });
    setTimeout(function () {
      var nameInput = $('#rfName');
      if (nameInput && !edit) nameInput.focus();
    }, 80);
  }

  function closeResidentForm(force) {
    if (state.form.submitting && !force) return;
    var overlay = $('#residentFormOverlay');
    if (!overlay || overlay.hidden) {
      state.form.open = false;
      return;
    }
    overlay.classList.remove('open');
    setTimeout(function () { overlay.hidden = true; }, 160);
    state.form.open = false;
    state.form.source = null;
    setFormError('');
  }

  function readFormPayload() {
    var name = formValue('rfName');
    var building = formValue('rfBuilding');
    if (!name) throw new Error('请填写住户姓名');
    if (!building) throw new Error('请填写楼栋');
    var ageText = formValue('rfAge');
    var age = ageText === '' ? 60 : Number(ageText);
    if (!isFinite(age) || age < 0 || age > 120) throw new Error('年龄需在 0-120 之间');
    var family = formValue('rfFamily');
    return {
      name: name,
      gender: formValue('rfGender') || '未知',
      age: Math.round(age),
      phone: formValue('rfPhone'),
      building: building,
      unit: formValue('rfUnit'),
      room: formValue('rfRoom'),
      tags: checkedValues($('#rfTagChoices')),
      family_contact: family,
      emergency_contact: formValue('rfEmergency'),
      care_level: formValue('rfCareLevel'),
      profile: {
        living_status: formValue('rfLiving'),
        health_status: formValue('rfHealth'),
        mobility: formValue('rfMobility'),
        family_members: family,
        service_needs: checkedValues($('#rfServiceChoices')),
        habits: formValue('rfHabits'),
        notes: formValue('rfNotes')
      }
    };
  }

  function formErrorText(err) {
    if (err && err.data && Array.isArray(err.data.detail)) {
      return err.data.detail.map(function (item) {
        var loc = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : '';
        return (loc ? loc + '：' : '') + (item.msg || '参数不合法');
      }).join('；');
    }
    if (window.API) return API.describeError(err);
    return (err && err.message) || '保存失败，请稍后重试';
  }

  function resetResidentFilters() {
    var risk = $('#communityRiskFilter');
    var presence = $('#communityPresenceFilter');
    var q = $('#communitySearch');
    if (risk) risk.value = '';
    if (presence) presence.value = '';
    if (q) q.value = '';
    readFilters();
  }

  async function submitResidentForm(e) {
    if (e) e.preventDefault();
    if (state.form.submitting) return;
    var payload;
    try {
      payload = readFormPayload();
    } catch (err) {
      setFormError(err.message || '请检查表单填写');
      return;
    }

    var isEdit = state.form.mode === 'edit';
    var residentId = state.form.residentId;
    state.form.submitting = true;
    setFormError('');
    var submitBtn = $('#residentFormSubmit');
    if (submitBtn) { submitBtn.disabled = true; submitBtn.classList.add('loading'); }

    try {
      var res = isEdit
        ? await API.communityUpdateResident(residentId, payload)
        : await API.communityCreateResident(payload);
      var resident = (res && res.resident) || {};
      var newId = resident.id || residentId;
      var level = resident.care_level || ((resident.profile && resident.profile.care_level) || payload.care_level || '');
      var who = resident.name || payload.name;
      toast(isEdit
        ? ('已更新 ' + who + ' 的住户画像' + (level ? '（关怀等级：' + level + '）' : '') + '，已重新评估风险与关怀建议')
        : ('已新增住户 ' + who + (level ? '，画像关怀等级：' + level : '') + '，档案已生效'), 'success');
      closeResidentForm(true);
      state.form.submitting = false;
      if (!isEdit) {
        /* 新增后把关键词定位到该住户，确保表格中能立即看到新档案 */
        resetResidentFilters();
        var searchInput = $('#communitySearch');
        if (searchInput) searchInput.value = payload.name;
        readFilters();
      }
      await loadAll(true);
      var communityPage = document.getElementById('page-community');
      if (newId && communityPage && communityPage.classList.contains('active')) openResident(newId);
    } catch (err) {
      setFormError('保存失败：' + formErrorText(err));
      state.form.submitting = false;
      if (submitBtn) { submitBtn.disabled = false; submitBtn.classList.remove('loading'); }
      return;
    }
    if (submitBtn) { submitBtn.disabled = false; submitBtn.classList.remove('loading'); }
  }

  /* ======================================================================
     十二、事件绑定
     ====================================================================== */
  function readFilters() {
    var risk = $('#communityRiskFilter');
    var presence = $('#communityPresenceFilter');
    var q = $('#communitySearch');
    state.residents.filters.risk = risk ? risk.value : '';
    state.residents.filters.presence = presence ? presence.value : '';
    state.residents.filters.q = q ? q.value.trim() : '';
  }

  function bind() {
    var refresh = $('#communityRefresh');
    if (refresh) refresh.addEventListener('click', function () { enter(true); });

    var scan = $('#communityScan');
    if (scan) scan.addEventListener('click', runScan);

    var addResident = $('#communityAddResident');
    if (addResident) addResident.addEventListener('click', function () { openResidentForm(null); });

    var risk = $('#communityRiskFilter');
    var presence = $('#communityPresenceFilter');
    if (risk) risk.addEventListener('change', function () { readFilters(); loadResidentsOnly(); });
    if (presence) presence.addEventListener('change', function () { readFilters(); loadResidentsOnly(); });

    var q = $('#communitySearch');
    if (q) {
      var onSearch = debounce(function () { readFilters(); loadResidentsOnly(); }, 380);
      q.addEventListener('input', onSearch);
      q.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { readFilters(); loadResidentsOnly(); }
      });
    }
    var apply = $('#communityFilterApply');
    if (apply) apply.addEventListener('click', function () { readFilters(); loadResidentsOnly(); });
    var reset = $('#communityFilterReset');
    if (reset) {
      reset.addEventListener('click', function () {
        if (risk) risk.value = '';
        if (presence) presence.value = '';
        if (q) q.value = '';
        readFilters();
        loadResidentsOnly();
      });
    }

    var alertList = $('#communityAlertList');
    if (alertList) {
      alertList.addEventListener('click', function (e) {
        var btn = e.target.closest('[data-ack-alert]');
        if (!btn) return;
        e.preventDefault();
        e.stopPropagation();
        ackAlert(btn.getAttribute('data-ack-alert'), btn);
      });
    }

    var tbody = $('#communityResidentRows');
    if (tbody) {
      tbody.addEventListener('click', function (e) {
        var exitBtn = e.target.closest('[data-exit-resident]');
        if (exitBtn) {
          e.preventDefault();
          e.stopPropagation();
          registerExit(exitBtn.getAttribute('data-exit-resident'), exitBtn);
          return;
        }
        var detailBtn = e.target.closest('[data-detail-resident]');
        if (detailBtn) {
          e.preventDefault();
          e.stopPropagation();
          openResident(detailBtn.getAttribute('data-detail-resident'));
          return;
        }
        var row = e.target.closest('tr[data-resident]');
        if (row) openResident(row.getAttribute('data-resident'));
      });
      tbody.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        var row = e.target.closest('tr[data-resident]');
        if (!row) return;
        e.preventDefault();
        openResident(row.getAttribute('data-resident'));
      });
    }

    var drawerClose = $('#residentDrawerClose');
    if (drawerClose) drawerClose.addEventListener('click', closeResidentDrawer);
    var drawerOverlay = $('#residentDrawerOverlay');
    if (drawerOverlay) drawerOverlay.addEventListener('click', closeResidentDrawer);

    var drawerEdit = $('#residentDrawerEdit');
    if (drawerEdit) {
      drawerEdit.addEventListener('click', function () {
        if (!state.drawer.detail || !state.drawer.detail.resident) {
          toast('住户详情尚未加载完成，请稍后重试', 'warn');
          return;
        }
        if (state.drawer.sample) {
          toast('当前为示例数据，编辑画像需要后端服务可用', 'warn');
          return;
        }
        openResidentForm(state.drawer.detail.resident);
      });
    }

    var form = $('#residentForm');
    if (form) form.addEventListener('submit', submitResidentForm);
    var formClose = $('#residentFormClose');
    if (formClose) formClose.addEventListener('click', function () { closeResidentForm(false); });
    var formCancel = $('#residentFormCancel');
    if (formCancel) formCancel.addEventListener('click', function () { closeResidentForm(false); });
    var formOverlay = $('#residentFormOverlay');
    if (formOverlay) {
      formOverlay.addEventListener('click', function (e) {
        if (e.target === formOverlay) closeResidentForm(false);
      });
    }

    var drawerBody = $('#residentDrawerBody');
    if (drawerBody) {
      drawerBody.addEventListener('click', function (e) {
        var exitBtn = e.target.closest('[data-drawer-exit]');
        if (exitBtn) {
          registerExit(exitBtn.getAttribute('data-drawer-exit'), exitBtn);
          return;
        }
        var retry = e.target.closest('[data-retry-resident]');
        if (retry) openResident(retry.getAttribute('data-retry-resident'));
      });
    }

    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      var formOverlay = $('#residentFormOverlay');
      if (state.form.open && formOverlay && !formOverlay.hidden) {
        closeResidentForm(false);
        return;
      }
      var drawer = $('#residentDrawer');
      if (drawer && drawer.classList.contains('open')) closeResidentDrawer();
    });
  }

  async function loadResidentsOnly() {
    var seq = ++state.seq;
    var residentRes = await fetchResidents();
    if (seq !== state.seq) return;
    state.residents.items = residentRes.data.items;
    state.residents.total = residentRes.data.total;
    state.residents.risk_counts = residentRes.data.risk_counts || {};
    state.residents.live = residentRes.live;
    state.loaded = true;
    renderResidents();
    renderSampleNote();
  }

  /* ======================================================================
     十二、自动刷新（60 秒，仅页面激活时执行）
     ====================================================================== */
  function stopTimer() {
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
  }

  function startTimer() {
    stopTimer();
    state.timer = setInterval(function () {
      var page = document.getElementById('page-community');
      if (!page || !page.classList.contains('active')) {
        stopTimer();
        return;
      }
      if (!state.loaded || state.loading) return;
      refreshOverviewAlerts(true);
    }, 60000);
  }

  /* ======================================================================
     十三、对外接口
     ====================================================================== */
  function enter(force) {
    startTimer();
    if (!state.loaded || force) {
      loadAll(force);
    } else {
      requestAnimationFrame(resizeCharts);
    }
  }

  function leave() {
    stopTimer();
    closeResidentForm(true);
    closeResidentDrawer();
  }

  function init() {
    if (!$('#page-community')) return;
    bind();
  }

  window.CommunityUI = {
    init: init,
    enter: enter,
    leave: leave,
    refresh: function () { return loadAll(true); },
    state: state
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
