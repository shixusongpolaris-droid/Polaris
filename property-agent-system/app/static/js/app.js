/* ==========================================================================
   app.js —— 页面路由、数据加载、渲染与交互
   结构：UI 工具 → 状态 → 导航 → 各页面渲染 → 工单抽屉 → 初始化
   ========================================================================== */
(function () {
  'use strict';

  /* ======================================================================
     一、基础工具
     ====================================================================== */
  var STATUS_MAP = {
    submitted: '已提交', understood: '已理解', assigned: '待接单', accepted: '已接单',
    processing: '处理中', completed: '已完工', returned: '退回整改', closed: '已闭环', cancelled: '已取消'
  };

  var URGENCY_CLASS = { '紧急': 'u-critical', '高': 'u-high', '中': 'u-mid', '低': 'u-low' };

  var AGENT_META = {
    understanding: { name: '报修理解 Agent', short: '理解', icon: 'eye' },
    dispatch: { name: '派单决策 Agent', short: '派单', icon: 'target' },
    monitoring: { name: '过程监控 Agent', short: '监控', icon: 'alert' },
    verification: { name: '闭环验证 Agent', short: '验证', icon: 'shield' },
    coordinator: { name: '工单编排中心', short: '编排', icon: 'flow' },
    community_guard: { name: '小区守护 Agent', short: '守护', icon: 'shield' },
    parking: { name: '停车收费 Agent', short: '停车', icon: 'car' },
    resident_profile: { name: '住户画像 Agent', short: '画像', icon: 'user' }
  };

  var WARN_TYPE_LABEL = {
    processing_timeout: '处理超时', accept_timeout: '接单超时', sla_warning: 'SLA 预警',
    verification_returned: '验证退回', escalated: '工单升级', timeout: '超时预警'
  };

  var ACTION_LABEL = {
    dispatch_decision: '派单决策', ticket_understood: '报修理解', sla_scan: 'SLA 扫描',
    completion_verified: '完工验证', trace_closed: '工单归档', accept: '接单', complete: '完工提交',
    ticket_created: '工单创建', monitor_scan: '监控扫描',
    analyze_repair_request: '报修理解', monitor_tickets: 'SLA 监控', verify_work_order: '完工验证',
    inactivity_scan: '住户巡检', parking_charge: '停车计费', parking_entry: '车辆入场',
    create_resident: '住户建档', update_resident: '画像更新', build_resident_profile: '画像生成'
  };

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function esc(str) {
    return String(str == null ? '' : str).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function svgIcon(name, cls) {
    return '<svg class="icon' + (cls ? ' ' + cls : '') + '"><use href="#i-' + name + '"></use></svg>';
  }

  function arr(v) { return Array.isArray(v) ? v : []; }
  function num(v) { var n = Number(v); return isFinite(n) ? n : null; }
  function fmtNum(n) {
    var v = num(n);
    if (v == null) return '—';
    return v.toLocaleString('zh-CN');
  }
  function fmtFixed(n, digits) {
    var v = num(n);
    if (v == null) return '—';
    return v.toFixed(digits == null ? 1 : digits);
  }
  function pad2(n) { return String(n).padStart(2, '0'); }

  function statusLabel(code, fallback) {
    return fallback || STATUS_MAP[code] || code || '未知';
  }
  function statusClass(code) {
    return 's-' + (STATUS_MAP[code] ? code : 'submitted');
  }
  function urgencyClass(urgency) {
    return URGENCY_CLASS[urgency] || 'u-none';
  }
  function shortTime(value) {
    if (!value) return '—';
    var s = String(value).replace('T', ' ');
    if (s.length >= 16) return s.slice(5, 16);
    return s;
  }
  function fullTime(value) {
    if (!value) return '—';
    return String(value).replace('T', ' ').slice(0, 19);
  }
  function durationLabel(ms) {
    var v = num(ms);
    if (v == null) return '—';
    return v >= 1000 ? (v / 1000).toFixed(2) + ' s' : v.toFixed(1) + ' ms';
  }
  function confidencePct(conf) {
    var v = num(conf);
    if (v == null) return null;
    return v <= 1 ? Math.round(v * 1000) / 10 : Math.round(v * 10) / 10;
  }

  function debounce(fn, wait) {
    var timer = null;
    return function () {
      var args = arguments, ctx = this;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(ctx, args); }, wait);
    };
  }

  function statusBadge(ticket) {
    if (!ticket) return '';
    var code = ticket.status || 'submitted';
    return '<span class="status-badge ' + statusClass(code) + '">' +
      esc(statusLabel(code, ticket.status_label)) + '</span>';
  }
  function urgencyBadge(name) {
    if (!name) return '<span class="urgency u-none">未知</span>';
    return '<span class="urgency ' + urgencyClass(name) + '">' + esc(name) + '</span>';
  }
  function agentTag(code) {
    var meta = AGENT_META[code] || AGENT_META.coordinator;
    return '<span class="agent-tag ' + (AGENT_META[code] ? code : 'coordinator') + '">' + esc(meta.name) + '</span>';
  }

  function emptyHTML(title, desc, iconName, actionHtml) {
    return '<div class="empty-state">' +
      '<div class="empty-icon">' + svgIcon(iconName || 'info') + '</div>' +
      '<strong>' + esc(title || '暂无数据') + '</strong>' +
      '<p>' + esc(desc || '接口暂无数据返回') + '</p>' +
      (actionHtml ? '<div class="empty-actions">' + actionHtml + '</div>' : '') +
      '</div>';
  }

  /* ------------------------------ 全局提示 ------------------------------ */
  var toastIcons = { success: 'check', error: 'alert', warn: 'alert', info: 'info' };

  function toast(message, type, duration) {
    var container = $('#toastContainer');
    if (!container) return;
    type = type || 'info';
    var el = document.createElement('div');
    el.className = 'toast ' + type;
    el.innerHTML = '<span class="toast-icon">' + svgIcon(toastIcons[type] || 'info') + '</span>' +
      '<div class="toast-text">' + esc(message) + '</div>' +
      '<button class="toast-close" type="button" aria-label="关闭提示">×</button>';
    container.appendChild(el);
    var timer = setTimeout(remove, duration || 3800);
    var close = el.querySelector('.toast-close');
    if (close) close.addEventListener('click', remove);
    function remove() {
      clearTimeout(timer);
      if (!el.parentNode) return;
      el.classList.add('out');
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 220);
    }
  }

  /* ------------------------------ 数字滚动 ------------------------------ */
  function countUp(el, value, decimals) {
    if (!el) return;
    var target = num(value);
    if (target == null) { el.textContent = '—'; return; }
    var digits = decimals || 0;
    function format(v) {
      return digits ? v.toFixed(digits) : Math.round(v).toLocaleString('zh-CN');
    }
    var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduceMotion || target === 0) { el.textContent = format(target); return; }
    var start = null;
    function frame(now) {
      if (start == null) start = now;
      var progress = Math.min(1, (now - start) / 900);
      var eased = 1 - Math.pow(1 - progress, 3);
      el.textContent = format(target * eased);
      if (progress < 1) requestAnimationFrame(frame);
      else el.textContent = format(target);
    }
    requestAnimationFrame(frame);
  }

  /* ======================================================================
     二、全局状态
     ====================================================================== */
  var state = {
    page: 'dashboard',
    loaded: { dashboard: false, tickets: false, logs: false, analytics: false, about: false, community: false, parking: false },
    sample: { dashboard: false, tickets: false, logs: false, analytics: false },
    error: { dashboard: null, tickets: null, logs: null, analytics: null },
    summary: null,
    dashboard: { summary: null },
    tickets: {
      items: [], total: 0, page: 1, pageSize: 20, sample: false, loading: false, seq: 0,
      filters: { status: 'all', urgency: '', q: '' }
    },
    drawer: { ticket: null, sample: false, id: null, hint: null },
    logs: { all: [], filtered: [], expanded: {}, filters: { agent: '', q: '' }, sample: false },
    analytics: { summary: null, evalData: null, typeStats: null, sample: false, errorText: '' },
    about: { health: null, error: null }
  };

  var ticketHints = new Map();

  window.UI = {
    escapeHtml: esc,
    icon: svgIcon,
    toast: toast,
    statusLabel: statusLabel,
    statusClass: statusClass,
    urgencyClass: urgencyClass,
    shortTime: shortTime,
    fullTime: fullTime,
    fmtNum: fmtNum,
    fmtFixed: fmtFixed,
    countUp: countUp,
    emptyHTML: emptyHTML,
    debounce: debounce
  };

  /* ======================================================================
     三、服务状态 / 日期 / 示例提示
     ====================================================================== */
  function setApiStatus(kind, text) {
    var chip = $('#apiStatusChip');
    if (chip) {
      chip.classList.remove('ok', 'off', 'down');
      if (kind) chip.classList.add(kind);
    }
    var label = $('#apiStatusText');
    if (label) label.textContent = text || '检测中';
    var engine = $('#engineStatusText');
    if (engine) {
      engine.textContent = kind === 'ok' ? '服务正常 · 7 个 Agent 在线'
        : kind === 'off' ? '接口降级 · 展示示例数据'
          : kind === 'down' ? '服务未连接 · 展示示例数据' : '连接检测中…';
    }
    var dot = $('.engine-dot');
    if (dot) dot.classList.toggle('offline', kind !== 'ok');
  }

  function setDates() {
    var now = new Date();
    var weekdays = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'];
    var full = now.getFullYear() + '年' + (now.getMonth() + 1) + '月' + now.getDate() + '日 ' + weekdays[now.getDay()];
    var topbar = $('#topbarDate');
    if (topbar) topbar.textContent = full;
    var dash = $('#dashDate');
    if (dash) dash.textContent = full;
    var welcomeH1 = $('.welcome h1');
    if (welcomeH1) {
      var h = now.getHours();
      var greeting = h < 6 ? '夜深了' : h < 9 ? '早上好' : h < 12 ? '上午好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好';
      welcomeH1.innerHTML = greeting + '，欢迎回到 <span class="grad-text">智修云</span>';
    }
  }

  function retryPageForId(id) {
    if (id === 'dashSampleNote') return 'dashboard';
    if (id === 'ticketsSample') return 'tickets';
    if (id === 'logsSample') return 'logs';
    if (id === 'analyticsSample') return 'analytics';
    return 'dashboard';
  }

  function applySampleNote(id, show, err, customText) {
    var el = document.getElementById(id);
    if (!el) return;
    el.hidden = !show;
    if (!show) { el.innerHTML = ''; return; }
    var errText = err ? API.describeError(err) : '';
    var page = retryPageForId(id);
    el.innerHTML = svgIcon('alert') +
      '<div><strong>示例数据 · 接口未就绪。</strong>' +
      esc(customText || (errText ? errText + '，当前展示演示占位数据；接口恢复后将自动使用真实数据。' : '当前展示演示占位数据；接口恢复后将自动使用真实数据。')) +
      '</div>' +
      '<div class="sample-note-actions">' +
      '<button class="btn ghost sm" type="button" data-retry-page="' + page + '">重试接口</button>' +
      '</div>';
  }

  /* ======================================================================
     四、页面导航
     ====================================================================== */
  var PAGES = {
    dashboard: { title: '总览', sub: '运营态势与 Agent 运行指标' },
    tickets: { title: '工单中心', sub: '工单流转与决策追踪' },
    community: { title: '小区守护', sub: '住户出入与独居关怀监测' },
    parking: { title: '停车管理', sub: '车辆进出识别与自动计费' },
    dispatch: { title: '智能派单', sub: '多智能体流水线实时协作' },
    logs: { title: '决策日志', sub: '每一次决策都可追溯' },
    analytics: { title: '数据分析', sub: '历史工单运营分析' },
    about: { title: '系统架构', sub: '7 个 Agent 与系统数据流' }
  };

  function switchPage(name, opts) {
    opts = opts || {};
    if (!PAGES[name]) name = 'dashboard';
    state.page = name;

    /* 离开社区运营页面时释放自动刷新定时器、关闭页内弹层 */
    if (name !== 'community' && window.CommunityUI) CommunityUI.leave();
    if (name !== 'parking' && window.ParkingUI) ParkingUI.leave();

    $$('.page').forEach(function (page) {
      page.classList.toggle('active', page.dataset.page === name);
    });
    $$('.nav-item').forEach(function (item) {
      item.classList.toggle('active', item.dataset.page === name);
    });
    var meta = PAGES[name];
    var title = $('#topbarPage');
    var sub = $('#topbarSub');
    if (title) title.textContent = meta.title;
    if (sub) sub.textContent = meta.sub;

    var app = $('#app');
    if (app) app.classList.remove('nav-open');

    if (!opts.skipHistory) {
      try { history.replaceState(null, '', '#' + name); } catch (e) { /* ignore */ }
    }

    if (!opts.initial) closeDrawer();

    var pageEl = document.getElementById('page-' + name);
    if (pageEl) {
      requestAnimationFrame(function () {
        if (window.Charts) Charts.resizeIn(pageEl);
      });
    }
    loadPage(name, false);
  }

  function loadPage(name, force) {
    if (name === 'dashboard') {
      if (!state.loaded.dashboard || force) loadDashboard(force);
    } else if (name === 'tickets') {
      if (!state.loaded.tickets || force) loadTickets(force);
    } else if (name === 'community') {
      if (window.CommunityUI) CommunityUI.enter(force);
    } else if (name === 'parking') {
      if (window.ParkingUI) ParkingUI.enter(force);
    } else if (name === 'logs') {
      if (!state.loaded.logs || force) loadLogs(force);
    } else if (name === 'analytics') {
      if (!state.loaded.analytics || force) loadAnalytics(force);
    } else if (name === 'about') {
      if (!state.loaded.about || force) loadAbout(force);
    } else if (name === 'dispatch') {
      if (window.Demo) Demo.enter();
    }
  }

  function refreshCurrentPage() {
    var btn = $('#globalRefresh');
    if (btn) {
      btn.classList.add('spinning');
      setTimeout(function () { btn.classList.remove('spinning'); }, 800);
    }
    if (state.page === 'dashboard') loadDashboard(true);
    else if (state.page === 'tickets') loadTickets(true);
    else if (state.page === 'community') { if (window.CommunityUI) CommunityUI.enter(true); }
    else if (state.page === 'parking') { if (window.ParkingUI) ParkingUI.enter(true); }
    else if (state.page === 'logs') loadLogs(true);
    else if (state.page === 'analytics') loadAnalytics(true);
    else if (state.page === 'about') loadAbout(true);
    else toast('智能派单页无需刷新，可直接提交报修或运行演示', 'info');
  }

  /* ======================================================================
     五、总览页
     ====================================================================== */
  function normalizeSummary(data) {
    data = data || {};
    return {
      generated_at: data.generated_at || '',
      historical: data.historical || {},
      live: data.live || {},
      agent: data.agent || {},
      trend: arr(data.trend),
      types: arr(data.types),
      facilities: arr(data.facilities),
      buildings: arr(data.buildings),
      workers: arr(data.workers),
      urgency: arr(data.urgency),
      hourly: arr(data.hourly),
      recent: arr(data.recent),
      insights: arr(data.insights)
    };
  }

  function normalizeTicket(t) {
    t = t || {};
    var out = {};
    Object.keys(t).forEach(function (k) { out[k] = t[k]; });
    out.id = String(t.id || t.ticket_id || '');
    out.description = t.description || t.title || '（无报修描述）';
    out.repair_type = t.repair_type || '其他报修';
    out.location = t.location || [t.building, t.unit, t.room].filter(Boolean).join('') || '—';
    out.status = t.status || 'submitted';
    out.urgency = t.urgency || '中';
    return out;
  }

  async function loadDashboard(force) {
    var sample = false, err = null, data = null;
    var refreshBtns = [$('#dashRefresh')];
    refreshBtns.forEach(function (b) { if (b) b.classList.add('spinning'); });
    try {
      data = await API.summary(30);
      if (!data || typeof data !== 'object') throw new Error('空响应');
    } catch (e) {
      err = e;
      sample = true;
      data = SampleData.summary(30);
      setApiStatus('off', '接口降级');
    }
    var normalized = normalizeSummary(data);
    state.summary = normalized;
    state.dashboard.summary = normalized;
    state.sample.dashboard = sample;
    state.error.dashboard = err;
    state.loaded.dashboard = true;
    renderDashboard(normalized, sample, err);
    refreshBtns.forEach(function (b) { if (b) b.classList.remove('spinning'); });
    if (!sample) {
      setApiStatus('ok', '服务正常');
      var engine = $('#engineStatusText');
      if (engine) engine.textContent = '服务正常 · 7 个 Agent 在线';
    }
  }

  function renderDashboard(summary, sample, err) {
    var hist = summary.historical || {};
    var live = summary.live || {};
    var agent = summary.agent || {};

    var generated = $('#dashGenerated');
    if (generated) generated.textContent = summary.generated_at ? '数据更新于 ' + summary.generated_at : '数据更新时间未知';
    var sampleChip = $('#dashSampleChip');
    if (sampleChip) sampleChip.hidden = !sample;
    applySampleNote('dashSampleNote', sample, err);

    var kpis = [
      { id: 'kpiTotal', value: hist.total, decimals: 0, foot: 'kpiTotalFoot',
        html: '<span class="trend up">' + svgIcon('trend-up') + '近 ' + esc(hist.period_days || 180) + ' 天</span><span class="trend-divider"></span><span>历史累计</span>' },
      { id: 'kpiClosedRate', value: hist.closed_rate, decimals: 1, foot: 'kpiClosedRateFoot',
        html: '<span class="trend flat">实时 ' + esc(fmtFixed(live.closed_rate, 1)) + '%</span><span class="trend-divider"></span><span>历史闭环率</span>' },
      { id: 'kpiTimeoutRate', value: hist.timeout_rate, decimals: 1, foot: 'kpiTimeoutRateFoot',
        html: '<span class="trend down">' + svgIcon('alert') + '实时超时 ' + esc(fmtNum(live.timeout)) + ' 单 · SLA 超时占比</span>' },
      { id: 'kpiDuration', value: hist.avg_duration, decimals: 1, foot: 'kpiDurationFoot',
        html: '<span class="trend flat">平均响应 ' + esc(fmtFixed(hist.avg_response, 1)) + ' 分钟</span><span class="trend-divider"></span><span>全流程</span>' },
      { id: 'kpiDispatchAcc', value: agent.dispatch_accuracy != null ? agent.dispatch_accuracy : hist.auto_dispatch_rate, decimals: 1, foot: 'kpiDispatchAccFoot',
        html: '<span class="trend up">人工改派 ' + esc(fmtFixed(agent.manual_reassign_rate, 1)) + '%' +
          (agent.sample_size != null ? ' · ' + esc(fmtNum(agent.sample_size)) + ' 条评估样本' : '') + '</span>' },
      { id: 'kpiOpen', value: live.open, decimals: 0, foot: 'kpiOpenFoot',
        html: '<span class="trend up">' + svgIcon('trend-up') + '今日新增 ' + esc(fmtNum(live.today)) + ' 单</span><span class="trend-divider"></span><span>实时待处理</span>' }
    ];
    kpis.forEach(function (k) {
      var el = document.getElementById(k.id);
      if (el) countUp(el, k.value, k.decimals);
      var foot = document.getElementById(k.foot);
      if (foot) foot.innerHTML = k.html;
    });

    var open = num(live.open);
    var badge = $('#navOpenBadge');
    if (badge) {
      if (open != null && open > 0) {
        badge.hidden = false;
        badge.textContent = open > 99 ? '99+' : String(open);
      } else {
        badge.hidden = true;
      }
    }

    Charts.trend($('#chartTrend'), summary.trend);
    Charts.agentBars($('#chartAgent'), agent);
    renderAgentDecision(agent);
    Charts.types($('#chartTypes'), summary.types, hist.total);
    Charts.buildings($('#chartBuildings'), summary.buildings);
    Charts.urgency($('#chartUrgency'), summary.urgency);
    renderWorkerRows(summary.workers);
    renderRecent(summary.recent);
  }

  function renderAgentDecision(agent) {
    var el = $('#agentDecision');
    if (!el) return;
    var ms = num(agent.avg_decision_ms);
    if (ms == null) {
      el.innerHTML = '<div class="agent-decision-head"><span>平均决策耗时</span><strong>—</strong></div>';
      return;
    }
    var width = Math.max(4, Math.min(100, (ms / 30) * 100));
    var good = ms <= 20;
    el.innerHTML =
      '<div class="agent-decision-head"><span>平均决策耗时</span>' +
      '<strong>' + esc(ms.toFixed(1)) + '<em>ms</em></strong></div>' +
      '<div class="metric-track"><i style="width:' + width.toFixed(1) + '%"></i></div>' +
      '<div class="agent-decision-foot"><span>目标 &lt; 20 ms</span><span style="color:' + (good ? 'var(--success)' : 'var(--warning)') + '">' +
      (good ? '优于目标' : '可继续优化') + '</span></div>';
  }

  function renderWorkerRows(workers) {
    var tbody = $('#workerRows');
    if (!tbody) return;
    var list = arr(workers).slice().sort(function (a, b) { return (num(b.orders) || 0) - (num(a.orders) || 0); }).slice(0, 8);
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="7">' + emptyHTML('暂无维修工数据', '接口未返回维修工效率统计', 'user') + '</td></tr>';
      return;
    }
    var maxOrders = Math.max.apply(null, list.map(function (w) { return num(w.orders) || 0; }).concat([1]));
    tbody.innerHTML = list.map(function (w, i) {
      var orders = num(w.orders) || 0;
      var sat = num(w.satisfaction);
      var rate = num(w.timeout_rate);
      return '<tr>' +
        '<td><span class="worker-rank ' + (i === 0 ? 'top1' : i === 1 ? 'top2' : i === 2 ? 'top3' : '') + '">' + (i + 1) + '</span></td>' +
        '<td><div class="worker-name"><strong>' + esc(w.name || '—') + '</strong>' +
        (w.rating != null ? '<span>综合评分 ' + esc(fmtFixed(w.rating, 1)) + '</span>' : '') + '</div></td>' +
        '<td>' + esc(w.trade || '—') + '</td>' +
        '<td class="num"><span class="cell-score"><b>' + esc(fmtNum(orders)) + '</b>' +
        '<span class="cell-bar"><i style="width:' + Math.round(orders / maxOrders * 100) + '%"></i></span></span></td>' +
        '<td class="num">' + esc(fmtFixed(w.avg_duration, 1)) + ' 分钟</td>' +
        '<td class="num"><span class="satisfaction">' + esc(fmtFixed(sat, 2)) + '<span class="muted" style="font-size:10px;color:var(--text-4)">/5</span></span></td>' +
        '<td class="num"><span class="cell-score"><b>' + esc(fmtFixed(rate, 1)) + '%</b>' +
        '<span class="cell-bar warn"><i style="width:' + Math.min(100, (rate || 0) * 6) + '%"></i></span></span></td>' +
        '</tr>';
    }).join('');
  }

  function renderRecent(recent) {
    var wrap = $('#recentList');
    if (!wrap) return;
    var list = arr(recent).slice(0, 6);
    if (!list.length) {
      wrap.innerHTML = '<div class="recent-empty">暂无最新工单，接口未返回数据</div>';
      return;
    }
    wrap.innerHTML = list.map(function (t) {
      var ticket = normalizeTicket(t);
      return '<div class="recent-item" data-ticket="' + esc(ticket.id) + '" role="button" tabindex="0">' +
        '<div class="recent-top"><strong>' + esc(ticket.repair_type || '报修工单') + '</strong>' + statusBadge(ticket) + '</div>' +
        '<div class="recent-meta">' +
        '<span>' + svgIcon('pin') + esc(ticket.location) + '</span>' +
        '<span>' + svgIcon('user') + esc(ticket.worker_name || '待派单') + '</span>' +
        '<span class="recent-id">' + esc(ticket.id) + '</span>' +
        '<span>' + esc(shortTime(ticket.created_at)) + '</span>' +
        '</div></div>';
    }).join('');
  }

  /* ======================================================================
     六、工单中心
     ====================================================================== */
  function readTicketFilters() {
    var status = $('#filterStatus');
    var urgency = $('#filterUrgency');
    var q = $('#filterQ');
    state.tickets.filters.status = status ? status.value : 'all';
    state.tickets.filters.urgency = urgency ? urgency.value : '';
    state.tickets.filters.q = q ? q.value.trim() : '';
  }

  function ticketQueryParams() {
    var f = state.tickets.filters;
    var params = {
      limit: state.tickets.pageSize,
      offset: (state.tickets.page - 1) * state.tickets.pageSize
    };
    if (f.status && f.status !== 'all') params.status = f.status;
    if (f.urgency) params.urgency = f.urgency;
    if (f.q) params.q = f.q;
    return params;
  }

  async function loadTickets(force) {
    var seq = ++state.tickets.seq;
    state.tickets.loading = true;
    renderTicketLoading();
    var params = ticketQueryParams();
    var data = null, sample = false, err = null;
    try {
      data = await API.tickets(params);
      if (!data) throw new Error('空响应');
    } catch (e) {
      err = e;
      sample = true;
      data = SampleData.ticketsPage(params);
      setApiStatus('off', '接口降级');
    }
    if (seq !== state.tickets.seq) return;
    state.tickets.loading = false;
    state.tickets.items = arr(data.items).map(normalizeTicket);
    state.tickets.total = num(data.total) || state.tickets.items.length;
    state.tickets.sample = sample;
    state.error.tickets = err;
    state.loaded.tickets = true;
    applySampleNote('ticketsSample', sample, err);
    renderTickets();
    if (!sample) setApiStatus('ok', '服务正常');
  }

  function renderTicketLoading() {
    var tbody = $('#ticketRows');
    if (!tbody) return;
    tbody.innerHTML = '<tr class="placeholder-row"><td colspan="9"><div class="loading-block"><span class="spinner"></span>正在加载工单…</div></td></tr>';
    var total = $('#ticketsTotal');
    if (total) total.textContent = '正在加载工单…';
    var info = $('#ticketsPageInfo');
    if (info) info.textContent = '—';
  }

  function renderTickets() {
    var tbody = $('#ticketRows');
    var totalEl = $('#ticketsTotal');
    var info = $('#ticketsPageInfo');
    var prev = $('#ticketsPrev');
    var next = $('#ticketsNext');
    if (!tbody) return;

    var total = state.tickets.total;
    var items = state.tickets.items;
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="9">' + emptyHTML(
        '暂无工单',
        state.tickets.sample ? '无法连接工单接口，且示例数据中没有匹配的工单' : '当前筛选条件下没有工单，试试调整状态或关键词',
        'ticket'
      ) + '</td></tr>';
    } else {
      tbody.innerHTML = items.map(function (t) {
        return '<tr data-ticket="' + esc(t.id) + '">' +
          '<td class="mono">' + esc(t.id) + '</td>' +
          '<td class="cell-desc"><strong>' + esc(t.description) + '</strong><span>' + esc(t.reporter_name || t.trace_id || '业主报修') + '</span></td>' +
          '<td>' + esc(t.repair_type) + '</td>' +
          '<td>' + esc(t.location) + '</td>' +
          '<td>' + urgencyBadge(t.urgency) + '</td>' +
          '<td>' + statusBadge(t) + '</td>' +
          '<td>' + esc(t.worker_name || '待派单') + '</td>' +
          '<td class="cell-time">' + esc(shortTime(t.deadline)) + '</td>' +
          '<td class="cell-time">' + esc(shortTime(t.created_at)) + '</td>' +
          '</tr>';
      }).join('');
    }
    if (totalEl) totalEl.textContent = '共 ' + fmtNum(total) + ' 条工单';
    var page = state.tickets.page;
    var pageCount = Math.max(1, Math.ceil(total / state.tickets.pageSize));
    if (info) info.textContent = total ? ('第 ' + page + ' / ' + pageCount + ' 页 · 每页 ' + state.tickets.pageSize + ' 条') : '—';
    if (prev) prev.disabled = page <= 1;
    if (next) next.disabled = page >= pageCount || total === 0;
  }

  function goTicketsPage(delta) {
    var page = state.tickets.page + delta;
    if (page < 1) return;
    var pageCount = Math.max(1, Math.ceil(state.tickets.total / state.tickets.pageSize));
    if (page > pageCount) return;
    state.tickets.page = page;
    loadTickets(false);
  }

  /* ======================================================================
     七、工单详情抽屉
     ====================================================================== */
  function rememberTicket(detail) {
    if (!detail || !detail.ticket || !detail.ticket.id) return;
    ticketHints.set(String(detail.ticket.id), detail);
    if (ticketHints.size > 30) {
      var firstKey = ticketHints.keys().next().value;
      ticketHints.delete(firstKey);
    }
  }

  function openDrawerUI(id) {
    var drawer = $('#ticketDrawer');
    var overlay = $('#drawerOverlay');
    if (!drawer || !overlay) return;
    drawer.classList.add('open');
    drawer.setAttribute('aria-hidden', 'false');
    overlay.classList.add('open');
    document.body.classList.add('drawer-open');
    var title = $('#drawerTitle');
    if (title) title.textContent = id || '工单详情';
    var badges = $('#drawerBadges');
    if (badges) badges.innerHTML = '';
    var body = $('#drawerBody');
    if (body) body.innerHTML = '<div class="drawer-loading"><span class="spinner"></span><span>正在加载工单与 Agent 决策记录…</span></div>';
  }

  function closeDrawer() {
    var drawer = $('#ticketDrawer');
    var overlay = $('#drawerOverlay');
    if (!drawer || !overlay) return;
    drawer.classList.remove('open');
    drawer.setAttribute('aria-hidden', 'true');
    overlay.classList.remove('open');
    document.body.classList.remove('drawer-open');
    state.drawer.id = null;
    state.drawer.ticket = null;
  }

  function hintToDetail(hint) {
    if (!hint) return null;
    if (hint.ticket && (arr(hint.events).length || arr(hint.logs).length)) return hint;
    if (hint.ticket && hint.ticket.is_demo && window.SampleData) {
      var demo = SampleData.demoDetail();
      if (demo && String(demo.ticket.id) === String(hint.ticket.id)) return demo;
    }
    if (hint.ticket) return { ticket: hint.ticket, events: [], logs: [], warnings: arr(hint.warnings) };
    return null;
  }

  async function openTicket(id, hint) {
    if (!id) return;
    id = String(id);
    if (state.page !== 'tickets') switchPage('tickets');
    state.drawer.id = id;
    state.drawer.hint = hint || ticketHints.get(id) || null;
    openDrawerUI(id);
    var data = null, sample = false, err = null;
    try {
      data = await API.ticket(id);
      if (!data || !data.ticket) throw new Error('工单不存在');
    } catch (e) {
      err = e;
      sample = true;
      data = hintToDetail(state.drawer.hint) || (window.SampleData ? SampleData.ticketDetail(id) : null);
      if (data && !data.ticket && data.id) data = { ticket: data, events: [], logs: [], warnings: [] };
    }
    if (state.drawer.id !== id) return;
    if (!data || !data.ticket) {
      state.drawer.sample = true;
      renderDrawerError(id, err);
      return;
    }
    data.ticket = normalizeTicket(data.ticket);
    data.events = arr(data.events);
    data.logs = arr(data.logs);
    data.warnings = arr(data.warnings);
    state.drawer.ticket = data;
    state.drawer.sample = sample || !!data.is_sample;
    renderTicketDetail(data, state.drawer.sample, err);
    if (!sample) setApiStatus('ok', '服务正常');
  }

  function renderDrawerError(id, err) {
    var body = $('#drawerBody');
    if (!body) return;
    body.innerHTML = '<div class="drawer-loading" style="padding-top:60px">' +
      emptyHTML('无法加载工单 ' + id,
        (err ? API.describeError(err) : '工单不存在或接口未就绪') + '。请确认后端已启动后重试。',
        'alert',
        '<button class="btn primary sm" type="button" data-retry-drawer="' + esc(id) + '">重新加载</button>') +
      '</div>';
  }

  function renderTicketDetail(detail, sample, err) {
    var ticket = detail.ticket;
    var events = detail.events;
    var logs = detail.logs;
    var warnings = detail.warnings;

    var title = $('#drawerTitle');
    if (title) title.textContent = ticket.id;
    var badges = $('#drawerBadges');
    if (badges) {
      badges.innerHTML = statusBadge(ticket) + urgencyBadge(ticket.urgency) +
        '<span class="badge-soft">' + esc(ticket.repair_type) + '</span>' +
        (ticket.is_demo ? '<span class="badge-soft amber">演示工单</span>' : '') +
        (sample ? '<span class="badge-soft amber">示例数据</span>' : '');
    }

    var body = $('#drawerBody');
    if (!body) return;
    var html = '';

    if (sample) {
      html += '<div class="sample-note" style="margin-bottom:14px">' + svgIcon('alert') +
        '<div><strong>示例数据。</strong>' + esc(err ? API.describeError(err) : '后端接口未就绪') +
        '，以下内容为演示占位；接单 / 完工操作需要后端服务可用。</div></div>';
    }

    html += renderBasicInfo(ticket);
    html += renderVerification(ticket);
    html += renderTimeline(events, ticket);
    html += renderAgentLogs(logs);
    html += renderWarnings(warnings);
    html += renderActionPanel(ticket, sample);
    body.innerHTML = html;
  }

  function renderBasicInfo(t) {
    var items = [
      ['工单号', t.id, true],
      ['报修类型', t.repair_type],
      ['紧急程度', t.urgency + '（评分 ' + fmtFixed(t.urgency_score, 2) + '）'],
      ['报修位置', t.location],
      ['维修工', (t.worker_name || '待派单') + (t.trade ? ' · ' + t.trade : '')],
      ['SLA 时限', t.sla_minutes != null ? t.sla_minutes + ' 分钟' : '—'],
      ['要求完成', shortTime(t.deadline)],
      ['创建时间', fullTime(t.created_at)],
      ['响应时长', t.response_minutes != null ? t.response_minutes + ' 分钟' : '—'],
      ['处理时长', t.duration_minutes != null ? t.duration_minutes + ' 分钟' : '—'],
      ['接单时间', fullTime(t.accepted_at)],
      ['闭环时间', fullTime(t.closed_at)],
      ['现场照片', (t.photos_count || 0) + ' 张'],
      ['业主满意度', t.satisfaction != null ? t.satisfaction : '—'],
      ['trace_id', t.trace_id || '—', true]
    ];
    var flags = [];
    if (t.auto_dispatched) flags.push('<span class="flag-pill ok">' + svgIcon('zap') + '自动派单</span>');
    if (t.reassigned) flags.push('<span class="flag-pill warn">' + svgIcon('refresh') + '人工改派</span>');
    if (t.escalated) flags.push('<span class="flag-pill danger">' + svgIcon('alert') + '已升级</span>');
    if (t.is_timeout) flags.push('<span class="flag-pill danger">' + svgIcon('clock') + 'SLA 超时</span>');
    if (t.verification_verdict) {
      flags.push('<span class="flag-pill ' + (t.verification_verdict === 'pass' ? 'ok' : 'danger') + '">' +
        svgIcon('shield') + (t.verification_verdict === 'pass' ? '验证通过' : '验证退回') + '</span>');
    }

    return '<div class="detail-section">' +
      '<div class="detail-section-head">' + svgIcon('ticket') + '<h4>工单基本信息</h4>' +
      '<span class="count">' + esc(t.status_label || statusLabel(t.status)) + '</span></div>' +
      '<div class="detail-card">' +
      '<div class="detail-grid">' + items.map(function (row) {
        return '<div class="detail-item"><span>' + esc(row[0]) + '</span><strong' + (row[2] ? ' class="mono"' : '') + '>' + esc(row[1]) + '</strong></div>';
      }).join('') +
      '</div>' +
      '<div class="detail-desc"><span>报修内容</span>' + esc(t.description) + '</div>' +
      (flags.length ? '<div class="detail-flags">' + flags.join('') + '</div>' : '') +
      '</div></div>';
  }

  function renderVerification(t) {
    if (!t.verification_verdict && !t.result_note) return '';
    var pass = t.verification_verdict === 'pass';
    var box = '';
    if (t.verification_verdict) {
      box = '<div class="verification-box ' + (pass ? 'pass' : 'fail') + '">' +
        '<strong>' + (pass ? '闭环验证通过' : '闭环验证未通过 · 需整改') + '</strong>' +
        esc(t.verification_note || '验证 Agent 已完成完工资料核验。') + '</div>';
    }
    return '<div class="detail-section">' +
      '<div class="detail-section-head">' + svgIcon('shield') + '<h4>完工与闭环验证</h4></div>' +
      '<div class="detail-card">' +
      (t.result_note ? '<div class="detail-desc" style="margin-top:0;padding-top:0;border-top:0"><span>完工说明</span>' + esc(t.result_note) + '</div>' : '') +
      box +
      '</div></div>';
  }

  function eventAgentCode(event) {
    var raw = String((event && (event.agent || event.actor)) || '').toLowerCase();
    if (AGENT_META[raw]) return raw;
    if (raw.indexOf('understand') >= 0) return 'understanding';
    if (raw.indexOf('dispatch') >= 0 || raw.indexOf('assign') >= 0) return 'dispatch';
    if (raw.indexOf('monitor') >= 0) return 'monitoring';
    if (raw.indexOf('verif') >= 0) return 'verification';
    if (raw.indexOf('coordinator') >= 0 || raw.indexOf('worker') >= 0) return 'coordinator';
    if (raw.indexOf('community') >= 0 || raw.indexOf('guard') >= 0) return 'community_guard';
    if (raw.indexOf('parking') >= 0 || raw.indexOf('charge') >= 0) return 'parking';
    if (raw.indexOf('profile') >= 0 || raw.indexOf('resident') >= 0) return 'resident_profile';
    var type = String((event && event.event_type) || '').toLowerCase();
    if (type.indexOf('understand') >= 0 || type.indexOf('parse') >= 0) return 'understanding';
    if (type.indexOf('dispatch') >= 0 || type.indexOf('assign') >= 0) return 'dispatch';
    if (type.indexOf('warning') >= 0 || type.indexOf('accept') >= 0 || type.indexOf('monitor') >= 0) return 'monitoring';
    if (type.indexOf('verif') >= 0 || type.indexOf('return') >= 0) return 'verification';
    return 'coordinator';
  }

  function deriveEvents(t) {
    var list = [];
    function add(ts, code, title, summary, from, to) {
      if (!ts) return;
      list.push({ ts: ts, agent: code, event_type: title, title: title, summary: summary, from_status: from, to_status: to, derived: true });
    }
    add(t.created_at, 'coordinator', '工单创建', '业主提交报修，系统生成工单与 trace_id。', null, 'submitted');
    add(t.understood_at, 'understanding', '报修理解完成', '识别报修类型「' + t.repair_type + '」，紧急程度「' + t.urgency + '」。', 'submitted', 'understood');
    add(t.assigned_at, 'dispatch', '智能派单完成', '派单至 ' + (t.worker_name || '—') + (t.trade ? '（' + t.trade + '）' : '') + '，SLA ' + (t.sla_minutes || '—') + ' 分钟。', 'understood', 'assigned');
    add(t.accepted_at, 'monitoring', '维修工已接单', '维修工确认接单，响应时长 ' + (t.response_minutes != null ? t.response_minutes + ' 分钟' : '—') + '。', 'assigned', 'accepted');
    add(t.completed_at, 'coordinator', '维修工提交完工', t.result_note || '维修工提交完工说明。', 'processing', 'completed');
    add(t.closed_at, 'verification', '工单闭环', t.verification_note || '闭环验证完成，工单归档。', 'completed', 'closed');
    list.sort(function (a, b) { return String(a.ts).localeCompare(String(b.ts)); });
    return list;
  }

  function renderTimeline(events, ticket) {
    var list = arr(events);
    var derived = false;
    if (!list.length) {
      list = deriveEvents(ticket);
      derived = list.length > 0;
    }
    var inner;
    if (!list.length) {
      inner = '<div class="detail-card">' + emptyHTML('暂无事件记录', '接口未返回事件时间线', 'clock') + '</div>';
    } else {
      inner = '<div class="detail-card"><div class="timeline">' + list.map(function (e) {
        var code = eventAgentCode(e);
        var title = e.title || e.event_type || '事件';
        var fromLabel = e.from_status ? statusLabel(e.from_status) : '';
        var toLabel = e.to_status ? statusLabel(e.to_status) : '';
        return '<div class="tl-item ' + code + '">' +
          '<span class="tl-dot"></span>' +
          '<div class="tl-head">' + agentTag(code) + '<strong>' + esc(title) + '</strong>' +
          '<span class="tl-time">' + esc(shortTime(e.ts || e.created_at)) + '</span></div>' +
          (e.summary ? '<div class="tl-summary">' + esc(e.summary) + '</div>' : '') +
          (fromLabel && toLabel ? '<div class="tl-transition">' + esc(fromLabel) + '<span class="arrow">→</span>' + esc(toLabel) + '</div>' : '') +
          '</div>';
      }).join('') + '</div>' +
        (derived ? '<div class="tl-note">事件流接口未返回，以上时间线由工单时间字段推导。</div>' : '') +
        '</div>';
    }
    return '<div class="detail-section">' +
      '<div class="detail-section-head">' + svgIcon('flow') + '<h4>Agent 决策时间线</h4>' +
      '<span class="count">' + list.length + ' 个节点</span></div>' +
      inner + '</div>';
  }

  function renderAgentLogs(logs) {
    var list = arr(logs);
    var inner;
    if (!list.length) {
      inner = '<div class="detail-card">' + emptyHTML('暂无决策日志', '该工单尚未产生 Agent 决策记录', 'list') + '</div>';
    } else {
      inner = '<div class="log-list">' + list.map(function (log) {
        var code = eventAgentCode({ agent: log.agent, event_type: log.action });
        var pct = confidencePct(log.confidence);
        var output = log.output;
        if (typeof output === 'string') {
          try { output = JSON.parse(output); } catch (e) { /* keep */ }
        }
        var json = output != null ? JSON.stringify(output, null, 2) : '（无 output）';
        return '<div class="log-item">' +
          '<div class="log-item-head">' + agentTag(code) +
          '<strong>' + esc(ACTION_LABEL[log.action] || log.action || '决策') + '</strong>' +
          '<span class="log-confidence">置信度 ' + (pct != null ? esc(pct.toFixed(1)) + '%' : '—') +
          (pct != null ? '<span class="progress ' + (pct >= 90 ? 'high' : pct >= 75 ? 'mid' : 'low') + '"><i style="width:' + Math.min(100, pct) + '%"></i></span>' : '') +
          '</span>' +
          '<span class="log-time">' + esc(shortTime(log.created_at)) + '</span></div>' +
          (log.rationale ? '<div class="log-rationale">' + esc(log.rationale) + '</div>' : '') +
          '<details><summary>查看决策 output JSON</summary><pre class="json-block">' + esc(json) + '</pre></details>' +
          '<div class="log-foot"><span>' + esc(AGENT_META[code] ? AGENT_META[code].name : code) + '</span>' +
          '<span>耗时 ' + esc(durationLabel(log.duration_ms)) + '</span>' +
          (log.input_summary ? '<span>输入：' + esc(String(log.input_summary).slice(0, 80)) + '</span>' : '') +
          '</div></div>';
      }).join('') + '</div>';
    }
    return '<div class="detail-section">' +
      '<div class="detail-section-head">' + svgIcon('list') + '<h4>Agent 决策日志</h4>' +
      '<span class="count">' + list.length + ' 条</span></div>' +
      inner + '</div>';
  }

  function renderWarnings(warnings) {
    var list = arr(warnings);
    var inner;
    if (!list.length) {
      inner = '<div class="detail-card">' + emptyHTML('暂无预警记录', '工单处理过程中未产生预警', 'shield') + '</div>';
    } else {
      inner = '<div class="warning-list">' + list.map(function (w) {
        var level = String(w.level || '').toLowerCase();
        var critical = level.indexOf('high') >= 0 || level.indexOf('critical') >= 0 || level.indexOf('紧急') >= 0 || level.indexOf('高') >= 0;
        var typeLabel = WARN_TYPE_LABEL[w.warning_type] || w.warning_type || '预警';
        return '<div class="warning-item' + (critical ? ' critical' : '') + '">' + svgIcon('alert') +
          '<div><strong>' + esc(typeLabel) + '</strong>' + esc(w.message || '') +
          '<small>' + esc(shortTime(w.created_at)) + (w.escalated_to ? ' · 已升级至 ' + esc(w.escalated_to) : '') +
          ' · 状态 ' + esc(w.status === 'open' ? '待处理' : (w.status || '—')) + '</small></div></div>';
      }).join('') + '</div>';
    }
    return '<div class="detail-section">' +
      '<div class="detail-section-head">' + svgIcon('alert') + '<h4>预警记录</h4>' +
      '<span class="count">' + list.length + ' 条</span></div>' +
      inner + '</div>';
  }

  function renderActionPanel(ticket, sample) {
    var status = ticket.status;
    var inner = '';
    var title = '工单操作';
    var iconName = 'wrench';

    if (status === 'assigned') {
      title = '等待维修工接单';
      inner = '<p>该工单已派单至 <strong>' + esc(ticket.worker_name || '维修工') + '</strong>，可在此模拟维修工接单，接单后将进入处理流程。</p>' +
        '<button class="btn primary" type="button" data-action="accept" data-ticket-id="' + esc(ticket.id) + '">' +
        svgIcon('check') + '标记接单</button>' +
        '<div class="action-result" id="drawerActionResult" hidden></div>' +
        '<div class="action-tip" style="margin-top:9px">' + svgIcon('info') +
        '接单会调用 <code>POST /api/tickets/{id}/accept</code>，成功后自动刷新工单与列表。</div>';
    } else if (status === 'accepted' || status === 'processing' || status === 'returned') {
      title = status === 'returned' ? '退回整改 · 重新提交完工' : '提交完工';
      iconName = status === 'returned' ? 'alert' : 'check';
      inner = '<p>填写完工说明与现场照片数量，提交后将自动触发闭环验证 Agent 验收；验证不通过会退回整改。</p>' +
        '<div class="complete-form">' +
        '<label><span>完工说明</span><textarea id="drawerResultNote" placeholder="例如：已更换爆裂管段并完成打压测试，现场清理完毕，业主确认恢复供水。"></textarea></label>' +
        '<div class="complete-row">' +
        '<label><span>现场照片数量</span><input id="drawerPhotos" type="number" min="0" max="9" step="1" value="' + esc(ticket.photos_count || 0) + '"></label>' +
        '<label><span>提交至</span><input value="闭环验证 Agent" disabled></label>' +
        '</div>' +
        '<button class="btn primary" type="button" data-action="complete" data-ticket-id="' + esc(ticket.id) + '">' +
        svgIcon('send') + '提交完工并触发验证</button>' +
        '</div>' +
        '<div class="action-result" id="drawerActionResult" hidden></div>' +
        '<div class="action-tip" style="margin-top:9px">' + svgIcon('info') +
        '接口：<code>POST /api/tickets/{id}/complete</code>，验证通过后工单自动闭环。</div>';
    } else if (status === 'closed') {
      title = '工单已闭环';
      iconName = 'shield';
      inner = '<p>该工单已通过闭环验证并归档，处理时长 ' + esc(ticket.duration_minutes != null ? ticket.duration_minutes + ' 分钟' : '—') +
        '，满意度 ' + esc(ticket.satisfaction != null ? ticket.satisfaction : '—') + '。</p>' +
        '<div class="verification-box pass"><strong>闭环完成</strong>可前往数据分析页查看该工单沉淀的运行指标与决策日志。</div>';
    } else if (status === 'completed') {
      title = '已完工 · 等待闭环验证';
      iconName = 'clock';
      inner = '<p>维修工已提交完工资料，闭环验证 Agent 正在处理，验证通过后工单将自动闭环。</p>';
    } else if (status === 'submitted' || status === 'understood') {
      title = 'Agent 受理中';
      iconName = 'robot';
      inner = '<p>工单已进入多智能体流水线，报修理解与派单决策 Agent 正在处理，派单完成后即可接单。</p>';
    } else if (status === 'cancelled') {
      title = '工单已取消';
      iconName = 'x';
      inner = '<p>该工单已取消，无需进一步处理。</p>';
    } else {
      title = '当前无可执行操作';
      inner = '<p>工单状态为「' + esc(statusLabel(status, ticket.status_label)) + '」，暂不支持在此操作。</p>';
    }

    if (sample) {
      inner += '<div class="action-tip" style="margin-top:10px;color:var(--warning)">' + svgIcon('alert') +
        '当前为示例数据，操作需要后端服务可用，否则会提示失败。</div>';
    }

    return '<div class="detail-section">' +
      '<div class="detail-section-head">' + svgIcon(iconName) + '<h4>' + esc(title) + '</h4></div>' +
      '<div class="action-panel"><h4>' + svgIcon(iconName) + esc(title) + '</h4>' + inner + '</div>' +
      '</div>';
  }

  function setDrawerActionMessage(type, message) {
    var el = $('#drawerActionResult');
    if (!el) return false;
    el.hidden = false;
    el.className = 'action-result ' + (type || 'err');
    el.textContent = message;
    return true;
  }

  async function acceptTicketFromDrawer(ticketId, btn) {
    if (btn) { btn.disabled = true; btn.classList.add('loading'); }
    try {
      await API.acceptTicket(ticketId, {});
      toast('接单成功，工单已进入处理流程', 'success');
      await refreshDrawerAndList(ticketId);
    } catch (e) {
      setDrawerActionMessage('err', '接单失败：' + API.describeError(e));
      toast('接单失败：' + API.describeError(e), 'error');
      if (btn) { btn.disabled = false; btn.classList.remove('loading'); }
    }
  }

  async function completeTicketFromDrawer(ticketId, btn) {
    var noteEl = $('#drawerResultNote');
    var photosEl = $('#drawerPhotos');
    var note = noteEl ? noteEl.value.trim() : '';
    var photos = photosEl ? Number(photosEl.value) : 0;
    if (!note) {
      setDrawerActionMessage('err', '请填写完工说明后再提交。');
      if (noteEl) noteEl.focus();
      return;
    }
    if (!isFinite(photos) || photos < 0 || photos > 9) {
      setDrawerActionMessage('err', '现场照片数量需在 0 - 9 之间。');
      return;
    }
    if (btn) { btn.disabled = true; btn.classList.add('loading'); }
    try {
      var res = await API.completeTicket(ticketId, { result_note: note, photos_count: Math.round(photos) });
      var verification = res && res.verification;
      var verdict = verification && (verification.verdict || verification.pass);
      var returned = verdict === 'returned' || verdict === false || (res && res.ticket && res.ticket.status === 'returned');
      if (returned) {
        toast('完工已提交，但闭环验证未通过，工单退回整改', 'warn', 4500);
      } else {
        toast('完工提交成功，闭环验证通过', 'success');
      }
      await refreshDrawerAndList(ticketId);
    } catch (e) {
      setDrawerActionMessage('err', '提交完工失败：' + API.describeError(e));
      toast('提交完工失败：' + API.describeError(e), 'error');
      if (btn) { btn.disabled = false; btn.classList.remove('loading'); }
    }
  }

  async function refreshDrawerAndList(ticketId) {
    await openTicket(ticketId, state.drawer.hint);
    if (state.loaded.tickets) loadTickets(true);
  }

  /* ======================================================================
     八、决策日志
     ====================================================================== */
  async function loadLogs(force) {
    var data = null, sample = false, err = null;
    try {
      data = await API.logs({ limit: 200, offset: 0 });
      if (!data || !Array.isArray(data.items)) throw new Error('日志响应格式异常');
    } catch (e) {
      err = e;
      sample = true;
      data = SampleData.logs({ limit: 200, offset: 0 });
      setApiStatus('off', '接口降级');
    }
    state.logs.all = arr(data.items).map(function (log) {
      var out = {};
      Object.keys(log || {}).forEach(function (k) { out[k] = log[k]; });
      out.id = log.id != null ? log.id : ('log-' + Math.random().toString(36).slice(2, 9));
      return out;
    });
    state.logs.sample = sample;
    state.error.logs = err;
    state.loaded.logs = true;
    applySampleNote('logsSample', sample, err);
    applyLogFilters();
    renderLogStats();
    if (!sample) setApiStatus('ok', '服务正常');
  }

  function applyLogFilters() {
    var f = state.logs.filters;
    var q = f.q.toLowerCase();
    state.logs.filtered = state.logs.all.filter(function (log) {
      if (f.agent && log.agent !== f.agent) return false;
      if (q) {
        var hay = [log.agent_name, log.action, log.input_summary, log.rationale, log.ticket_id, log.trace_id]
          .join(' ').toLowerCase();
        if (hay.indexOf(q) < 0) return false;
      }
      return true;
    });
    renderLogs();
  }

  function renderLogStats() {
    var wrap = $('#logStats');
    if (!wrap) return;
    var defs = ['understanding', 'dispatch', 'monitoring', 'verification'];
    var html = defs.map(function (code) {
      var meta = AGENT_META[code];
      var logs = state.logs.all.filter(function (l) { return l.agent === code; });
      var count = logs.length;
      var confs = logs.map(function (l) { var c = confidencePct(l.confidence); return c == null ? null : c; }).filter(function (v) { return v != null; });
      var durs = logs.map(function (l) { return num(l.duration_ms); }).filter(function (v) { return v != null; });
      var avgConf = confs.length ? confs.reduce(function (a, b) { return a + b; }, 0) / confs.length : null;
      var avgDur = durs.length ? durs.reduce(function (a, b) { return a + b; }, 0) / durs.length : null;
      var last = logs.length ? (logs[0].created_at || '') : '';
      return '<div class="stat-card" data-agent="' + code + '">' +
        '<div class="stat-card-top"><span class="stat-card-title">' +
        '<span class="stat-card-icon">' + svgIcon(meta.icon) + '</span>' + esc(meta.name) + '</span>' +
        '<span class="badge-soft">' + (avgConf != null ? esc(avgConf.toFixed(1)) + '% 置信' : '—') + '</span></div>' +
        '<div class="stat-card-value">' + esc(fmtNum(count)) + '<small>次决策</small></div>' +
        '<div class="stat-card-foot"><span>平均耗时 ' + (avgDur != null ? esc(avgDur.toFixed(1)) + ' ms' : '—') + '</span>' +
        '<span>最近 ' + esc(last ? shortTime(last) : '—') + '</span></div>' +
        '</div>';
    }).join('');
    wrap.innerHTML = html;
  }

  function renderLogs() {
    var tbody = $('#logRows');
    if (!tbody) return;
    var items = state.logs.filtered;
    var meta = $('#logsMeta');
    if (meta) {
      var agentsCovered = {};
      items.forEach(function (l) { if (l.agent) agentsCovered[l.agent] = 1; });
      meta.textContent = '共 ' + fmtNum(items.length) + ' 条决策记录 · 覆盖 ' + Object.keys(agentsCovered).length + ' 个 Agent';
    }
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="7">' + emptyHTML(
        '暂无决策日志',
        state.logs.sample ? '无法连接日志接口，且示例数据中没有匹配记录' : '当前筛选条件下没有决策记录',
        'list'
      ) + '</td></tr>';
      return;
    }
    tbody.innerHTML = items.map(function (log) {
      var code = AGENT_META[log.agent] ? log.agent : eventAgentCode({ agent: log.agent, event_type: log.action });
      var pct = confidencePct(log.confidence);
      var expanded = !!state.logs.expanded[log.id];
      var actionLabel = ACTION_LABEL[log.action] || log.action || '决策';
      var summary = log.input_summary || log.rationale || '—';
      var row = '<tr class="log-row' + (expanded ? ' open' : '') + '" data-log-id="' + esc(log.id) + '">' +
        '<td class="cell-time">' + esc(shortTime(log.created_at)) + '</td>' +
        '<td><span class="agent-cell"><i class="agent-dot ' + code + '"></i>' + esc(AGENT_META[code] ? AGENT_META[code].name : (log.agent_name || code)) + '</span></td>' +
        '<td><span class="action-name">' + esc(actionLabel) + '</span></td>' +
        '<td><span class="mono" data-log-ticket="' + esc(log.ticket_id || '') + '" style="cursor:pointer;color:var(--primary)">' + esc(log.ticket_id || '—') + '</span></td>' +
        '<td class="cell-desc" style="max-width:280px;white-space:normal">' + esc(String(summary).slice(0, 90)) + '</td>' +
        '<td><span class="confidence-cell">' +
        '<span class="progress ' + (pct == null ? '' : pct >= 90 ? 'high' : pct >= 75 ? 'mid' : 'low') + '"><i style="width:' + (pct == null ? 0 : Math.min(100, pct)) + '%"></i></span>' +
        '<span>' + (pct != null ? esc(pct.toFixed(1)) + '%' : '—') + '</span></span></td>' +
        '<td class="num duration-cell">' + esc(durationLabel(log.duration_ms)) + '</td>' +
        '</tr>';
      if (!expanded) return row;
      var output = log.output;
      if (typeof output === 'string') {
        try { output = JSON.parse(output); } catch (e) { /* keep */ }
      }
      var json = output != null ? JSON.stringify(output, null, 2) : '（无 output）';
      var detail = '<tr class="log-detail-row"><td colspan="7"><div class="log-detail">' +
        '<h5>决策依据 rationale</h5>' +
        '<div class="rationale">' + esc(log.rationale || '（无决策依据）') + '</div>' +
        '<div class="summary-line"><b>输入摘要：</b>' + esc(log.input_summary || '—') + '</div>' +
        '<details><summary>查看 output JSON（已格式化）</summary><pre class="json-block">' + esc(json) + '</pre></details>' +
        '<div class="log-meta-chips">' +
        '<span>Agent：' + esc(log.agent_name || code) + '</span>' +
        '<span>动作：' + esc(log.action || '—') + '</span>' +
        '<span>置信度：' + (pct != null ? esc(pct.toFixed(1)) + '%' : '—') + '</span>' +
        '<span>耗时：' + esc(durationLabel(log.duration_ms)) + '</span>' +
        '<span>trace：' + esc(log.trace_id || '—') + '</span>' +
        '</div></div></td></tr>';
      return row + detail;
    }).join('');
  }

  function toggleLogRow(id) {
    if (!id) return;
    if (state.logs.expanded[id]) delete state.logs.expanded[id];
    else state.logs.expanded[id] = true;
    renderLogs();
  }

  /* ======================================================================
     九、数据分析
     ====================================================================== */
  function monthlyFromTrend(trend) {
    var map = {};
    arr(trend).forEach(function (d) {
      var month = String(d.date || '').slice(0, 7);
      if (!month) return;
      if (!map[month]) map[month] = { month: month, orders: 0, closed: 0, timeout: 0 };
      map[month].orders += num(d.orders) || 0;
      map[month].closed += num(d.closed) || 0;
      map[month].timeout += num(d.timeout) || 0;
    });
    return Object.keys(map).sort().map(function (m) {
      var o = map[m];
      return {
        month: m,
        orders: o.orders,
        closed_rate: o.orders ? Math.round(o.closed / o.orders * 1000) / 10 : 0,
        timeout_rate: o.orders ? Math.round(o.timeout / o.orders * 1000) / 10 : 0
      };
    });
  }

  function groupMonthly(monthly) {
    return arr(monthly).map(function (m) {
      return {
        month: m.month || m.name || '',
        orders: num(m.orders != null ? m.orders : m.count) || 0,
        closed_rate: num(m.closed_rate) != null ? num(m.closed_rate) : null,
        timeout_rate: num(m.timeout_rate)
      };
    }).filter(function (m) { return m.month; });
  }

  function typeStatsItems(ts) {
    ts = ts || {};
    if (arr(ts.types).length) return ts.types;
    if (arr(ts.items).length) return ts.items;
    if (arr(ts.per_type).length) return ts.per_type;
    return [];
  }

  async function loadAnalytics(force) {
    var errors = [];
    var summaryPromise = API.summary(180).catch(function (e) { errors.push(e); return null; });
    var evalPromise = API.agentEval().catch(function (e) { errors.push(e); return null; });
    var typePromise = API.typeStats().catch(function (e) { errors.push(e); return null; });
    var results = await Promise.all([summaryPromise, evalPromise, typePromise]);
    var summaryRaw = results[0], evalRaw = results[1], typeRaw = results[2];

    var sample = errors.length > 0;
    if (!summaryRaw) summaryRaw = SampleData.summary(180);
    if (!evalRaw) evalRaw = SampleData.agentEval();
    if (!typeRaw) typeRaw = SampleData.typeStats();
    state.analytics.summary = normalizeSummary(summaryRaw);
    state.analytics.evalData = evalRaw || {};
    state.analytics.typeStats = typeRaw || {};
    state.analytics.sample = sample || !!state.analytics.summary.is_sample;
    state.analytics.errorText = errors.length ? API.describeError(errors[0]) : '';
    state.loaded.analytics = true;
    applySampleNote('analyticsSample', state.analytics.sample, errors[0], errors.length
      ? errors.map(function (e) { return API.describeError(e); }).filter(function (v, i, a) { return a.indexOf(v) === i; }).join('；') +
      '，缺失部分使用示例数据占位，接口恢复后自动切换真实数据。'
      : '');
    renderAnalytics();
    if (!sample) setApiStatus('ok', '服务正常');
  }

  function renderAnalytics() {
    var summary = state.analytics.summary || normalizeSummary({});
    var evalData = state.analytics.evalData || {};
    var typeStats = state.analytics.typeStats || {};
    var hist = summary.historical || {};

    var sampleSize = num(evalData.sample_size) != null ? evalData.sample_size : hist.total;
    var anaSample = $('#anaSample');
    if (anaSample) anaSample.innerHTML = esc(fmtNum(sampleSize)) + '<em>单</em>';
    var anaClosed = $('#anaClosedRate');
    if (anaClosed) anaClosed.innerHTML = esc(fmtFixed(hist.closed_rate, 1)) + '<em>%</em>';
    var anaDuration = $('#anaDuration');
    if (anaDuration) anaDuration.innerHTML = esc(fmtFixed(hist.avg_duration, 1)) + '<em>分钟</em>';
    var anaSat = $('#anaSatisfaction');
    if (anaSat) anaSat.innerHTML = esc(fmtFixed(hist.satisfaction, 2)) + '<em>/5</em>';

    var typesData = summary.types.length ? summary.types
      : typeStatsItems(typeStats).map(function (t) { return { name: t.name || t.repair_type, value: t.count != null ? t.count : t.value }; });
    Charts.types($('#chartAnaTypes'), typesData, hist.total);

    var monthly = groupMonthly(typeStats.monthly || typeStats.by_month);
    if (!monthly.length) monthly = monthlyFromTrend(summary.trend);
    Charts.monthly($('#chartAnaMonthly'), monthly);

    var timeoutData = typeStatsItems(typeStats).map(function (t) {
      return { name: t.name || t.repair_type, timeout_rate: t.timeout_rate };
    }).filter(function (t) { return t.name && t.timeout_rate != null; });
    if (!timeoutData.length) {
      timeoutData = summary.buildings.map(function (b) { return { name: b.name, timeout_rate: b.timeout_rate }; });
    }
    Charts.timeoutCompare($('#chartAnaTimeout'), timeoutData);

    Charts.buildingRank($('#chartAnaBuildings'), summary.buildings);
    Charts.workerScatter($('#chartAnaWorkers'), summary.workers);
    Charts.hourly($('#chartAnaHourly'), summary.hourly);

    renderInsights(summary, evalData, typeStats);
  }

  function renderInsights(summary, evalData, typeStats) {
    var wrap = $('#insightList');
    if (!wrap) return;
    var insights = [];
    [summary.insights, evalData.insights, typeStats.insights].forEach(function (list) {
      arr(list).forEach(function (text) {
        if (text && typeof text === 'string') insights.push({ text: text, source: 'api' });
      });
    });
    insights = insights.filter(function (item, index, list) {
      return list.findIndex(function (x) { return x.text === item.text; }) === index;
    });
    if (!insights.length) {
      insights = buildComputedInsights(summary, evalData, typeStats).map(function (text) {
        return { text: text, source: 'computed' };
      });
    }
    insights = insights.slice(0, 6);
    wrap.innerHTML = insights.map(function (item, i) {
      var content = item.source === 'api' ? esc(item.text) : item.text;
      return '<div class="insight-item"><span class="insight-index">' + pad2(i + 1) + '</span><p>' + content + '</p></div>';
    }).join('');
    var source = $('#insightSource');
    if (source) {
      var sampleSize = num(evalData.sample_size) || num((summary.historical || {}).total);
      source.textContent = '数据来源：历史工单库' + (sampleSize ? ' · 样本 ' + fmtNum(sampleSize) + ' 条' : '') + (state.analytics.sample ? ' · 含示例数据' : '');
    }
  }

  function buildComputedInsights(summary, evalData, typeStats) {
    var list = [];
    var hist = summary.historical || {};
    var agent = summary.agent || {};
    var metrics = evalData.metrics || agent;
    if (hist.closed_rate != null) {
      list.push('历史 <strong>闭环率 ' + esc(fmtFixed(hist.closed_rate, 1)) + '%</strong>，超时率 <strong>' +
        esc(fmtFixed(hist.timeout_rate, 1)) + '%</strong>，平均处理时长 ' + esc(fmtFixed(hist.avg_duration, 1)) +
        ' 分钟，整体流转处于健康区间。');
    }
    var types = summary.types.slice().sort(function (a, b) { return (num(b.value) || 0) - (num(a.value) || 0); });
    if (types.length) {
      var totalTypes = types.reduce(function (s, t) { return s + (num(t.value) || 0); }, 0);
      list.push('报修类型中 <strong>' + esc(types[0].name) + '</strong> 占比最高（' + esc(fmtNum(types[0].value)) + ' 单，约 ' +
        (totalTypes ? Math.round(types[0].value / totalTypes * 100) : 0) + '%），建议优先保障对应工种值班力量。');
    }
    var buildings = summary.buildings.slice().sort(function (a, b) { return (num(b.timeout_rate) || 0) - (num(a.timeout_rate) || 0); });
    if (buildings.length) {
      list.push('楼栋超时率最高为 <strong>' + esc(buildings[0].name) + '</strong>（' + esc(fmtFixed(buildings[0].timeout_rate, 1)) +
        '%），可结合楼栋工单量 ' + esc(fmtNum(buildings[0].value)) + ' 单优化巡检与驻点安排。');
    }
    var workers = summary.workers.slice().sort(function (a, b) { return (num(b.orders) || 0) - (num(a.orders) || 0); });
    if (workers.length) {
      list.push('维修工 <strong>' + esc(workers[0].name) + '</strong> 工单量最高（' + esc(fmtNum(workers[0].orders)) +
        ' 单），平均时长 ' + esc(fmtFixed(workers[0].avg_duration, 1)) + ' 分钟、满意度 ' +
        esc(fmtFixed(workers[0].satisfaction, 2)) + '，可作为派单优先对象。');
    }
    if (metrics && (metrics.understanding_accuracy != null || metrics.dispatch_accuracy != null)) {
      list.push('Agent 侧：理解准确率 <strong>' + esc(fmtFixed(metrics.understanding_accuracy, 1)) + '%</strong>，派单准确率 <strong>' +
        esc(fmtFixed(metrics.dispatch_accuracy, 1)) + '%</strong>，平均决策耗时 ' + esc(fmtFixed(metrics.avg_decision_ms, 1)) + ' ms。');
    }
    if (!list.length) {
      list.push('暂无足够的历史数据生成分析结论，等待后端分析接口返回结果。');
    }
    return list;
  }

  /* ======================================================================
     十、系统架构
     ====================================================================== */
  async function loadAbout(force) {
    var health = null, healthErr = null, summaryRaw = null, summaryErr = null;
    try {
      health = await API.health();
    } catch (e) {
      healthErr = e;
    }
    if (!state.summary) {
      try {
        summaryRaw = await API.summary(30);
        state.summary = normalizeSummary(summaryRaw);
      } catch (e) {
        summaryErr = e;
      }
    }
    state.about.health = health;
    state.about.error = healthErr;
    state.loaded.about = true;
    renderAboutHealth(health, healthErr);
    renderAboutMetrics(state.summary);
    if (health) setApiStatus('ok', '服务正常');
    else if (healthErr) setApiStatus(healthErr.kind === 'http' ? 'off' : 'down', healthErr.kind === 'http' ? '接口降级' : '服务未连接');
  }

  function renderAboutHealth(health, err) {
    var wrap = $('#aboutHealth');
    if (!wrap) return;
    var ok = !!health;
    var text = ok ? '正常 · v' + (health.version || '1.0.0') : (err && err.kind === 'http' ? '接口异常' : '未连接');
    var sub = ok
      ? '数据库 ' + esc(health.db || 'property.db') + ' · 历史工单 ' + esc(fmtNum(health.history_orders)) + ' 条'
      : esc(err ? API.describeError(err) : '无法连接后端服务') + '，页面已启用示例数据降级。';
    wrap.innerHTML = '<div class="health-row ' + (ok ? 'ok' : 'down') + '"><span class="status-dot"></span>' +
      '<span>API 健康检查</span><strong>' + esc(text) + '</strong></div>' +
      '<div class="health-sub">' + sub + '</div>';
    var healthText = $('#healthStatusText');
    if (healthText) healthText.textContent = text;
  }

  function renderAboutMetrics(summary) {
    if (!summary) return;
    var hist = summary.historical || {};
    var agent = summary.agent || {};
    var set = function (id, value) {
      var el = document.getElementById(id);
      if (el) el.textContent = value;
    };
    set('aboutHistory', fmtNum(hist.total) + ' 单');
    set('aboutDispatchAcc', fmtFixed(agent.dispatch_accuracy != null ? agent.dispatch_accuracy : hist.auto_dispatch_rate, 1) + '%');
    set('aboutDecisionMs', fmtFixed(agent.avg_decision_ms, 1) + ' ms');
  }

  /* ======================================================================
     十一、事件绑定与初始化
     ====================================================================== */
  function bindNav() {
    $$('.nav-item').forEach(function (item) {
      item.addEventListener('click', function (e) {
        e.preventDefault();
        switchPage(item.dataset.page);
      });
    });
    var menuBtn = $('#menuBtn');
    var overlay = $('#sidebarOverlay');
    if (menuBtn) {
      menuBtn.addEventListener('click', function () {
        var app = $('#app');
        if (window.innerWidth <= 1280) {
          var open = app.classList.toggle('nav-open');
          menuBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
        } else {
          var collapsed = app.classList.toggle('sidebar-collapsed');
          menuBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
          setTimeout(function () { Charts.resizeAll(); }, 260);
        }
      });
    }
    if (overlay) {
      overlay.addEventListener('click', function () {
        $('#app').classList.remove('nav-open');
      });
    }
    window.addEventListener('hashchange', function () {
      var name = location.hash.replace('#', '');
      if (PAGES[name] && name !== state.page) switchPage(name, { skipHistory: true });
    });
  }

  function bindGlobal() {
    var refresh = $('#globalRefresh');
    if (refresh) refresh.addEventListener('click', refreshCurrentPage);
    var dashRefresh = $('#dashRefresh');
    if (dashRefresh) dashRefresh.addEventListener('click', function () { loadDashboard(true); });
    var recentMore = $('#recentMore');
    if (recentMore) recentMore.addEventListener('click', function () { switchPage('tickets'); });
    var recentList = $('#recentList');
    if (recentList) {
      recentList.addEventListener('click', function (e) {
        var item = e.target.closest('[data-ticket]');
        if (item) openTicket(item.dataset.ticket);
      });
      recentList.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        var item = e.target.closest('[data-ticket]');
        if (item) { e.preventDefault(); openTicket(item.dataset.ticket); }
      });
    }
    document.addEventListener('click', function (e) {
      var retry = e.target.closest('[data-retry-page]');
      if (retry) {
        var page = retry.getAttribute('data-retry-page');
        loadPage(page, true);
      }
    });
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      if ($('#ticketDrawer') && $('#ticketDrawer').classList.contains('open')) closeDrawer();
      else if ($('#app') && $('#app').classList.contains('nav-open')) $('#app').classList.remove('nav-open');
    });
  }

  function bindTickets() {
    var status = $('#filterStatus');
    var urgency = $('#filterUrgency');
    var q = $('#filterQ');
    if (status) status.addEventListener('change', function () { readTicketFilters(); state.tickets.page = 1; loadTickets(false); });
    if (urgency) urgency.addEventListener('change', function () { readTicketFilters(); state.tickets.page = 1; loadTickets(false); });
    if (q) {
      var onSearch = debounce(function () { readTicketFilters(); state.tickets.page = 1; loadTickets(false); }, 380);
      q.addEventListener('input', onSearch);
      q.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { readTicketFilters(); state.tickets.page = 1; loadTickets(false); }
      });
    }
    var apply = $('#filterApply');
    if (apply) apply.addEventListener('click', function () { readTicketFilters(); state.tickets.page = 1; loadTickets(false); });
    var reset = $('#filterReset');
    if (reset) {
      reset.addEventListener('click', function () {
        if (status) status.value = 'all';
        if (urgency) urgency.value = '';
        if (q) q.value = '';
        readTicketFilters();
        state.tickets.page = 1;
        loadTickets(false);
      });
    }
    var refresh = $('#ticketsRefresh');
    if (refresh) refresh.addEventListener('click', function () { loadTickets(true); });
    var newBtn = $('#ticketsNew');
    if (newBtn) {
      newBtn.addEventListener('click', function () {
        switchPage('dispatch');
        setTimeout(function () { var ta = $('#dispatchDesc'); if (ta) ta.focus(); }, 120);
      });
    }
    var prev = $('#ticketsPrev');
    if (prev) prev.addEventListener('click', function () { goTicketsPage(-1); });
    var next = $('#ticketsNext');
    if (next) next.addEventListener('click', function () { goTicketsPage(1); });
    var tbody = $('#ticketRows');
    if (tbody) {
      tbody.addEventListener('click', function (e) {
        var row = e.target.closest('tr[data-ticket]');
        if (row) openTicket(row.dataset.ticket);
      });
    }
    var drawerClose = $('#drawerClose');
    if (drawerClose) drawerClose.addEventListener('click', closeDrawer);
    var drawerOverlay = $('#drawerOverlay');
    if (drawerOverlay) drawerOverlay.addEventListener('click', closeDrawer);
    var drawerBody = $('#drawerBody');
    if (drawerBody) {
      drawerBody.addEventListener('click', function (e) {
        var acceptBtn = e.target.closest('[data-action="accept"]');
        if (acceptBtn) {
          acceptTicketFromDrawer(acceptBtn.getAttribute('data-ticket-id'), acceptBtn);
          return;
        }
        var completeBtn = e.target.closest('[data-action="complete"]');
        if (completeBtn) {
          completeTicketFromDrawer(completeBtn.getAttribute('data-ticket-id'), completeBtn);
          return;
        }
        var retry = e.target.closest('[data-retry-drawer]');
        if (retry) openTicket(retry.getAttribute('data-retry-drawer'));
      });
    }
  }

  function bindLogs() {
    var agentFilter = $('#logAgentFilter');
    var q = $('#logQ');
    if (agentFilter) {
      agentFilter.addEventListener('change', function () {
        state.logs.filters.agent = agentFilter.value;
        applyLogFilters();
      });
    }
    if (q) {
      var onSearch = debounce(function () {
        state.logs.filters.q = q.value.trim();
        applyLogFilters();
      }, 320);
      q.addEventListener('input', onSearch);
      q.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { state.logs.filters.q = q.value.trim(); applyLogFilters(); }
      });
    }
    var apply = $('#logFilterApply');
    if (apply) {
      apply.addEventListener('click', function () {
        state.logs.filters.agent = agentFilter ? agentFilter.value : '';
        state.logs.filters.q = q ? q.value.trim() : '';
        applyLogFilters();
      });
    }
    var reset = $('#logFilterReset');
    if (reset) {
      reset.addEventListener('click', function () {
        state.logs.filters = { agent: '', q: '' };
        if (agentFilter) agentFilter.value = '';
        if (q) q.value = '';
        applyLogFilters();
      });
    }
    var refresh = $('#logsRefresh');
    if (refresh) refresh.addEventListener('click', function () { loadLogs(true); });
    var tbody = $('#logRows');
    if (tbody) {
      tbody.addEventListener('click', function (e) {
        var ticketEl = e.target.closest('[data-log-ticket]');
        if (ticketEl) {
          var ticketId = ticketEl.getAttribute('data-log-ticket');
          if (ticketId) { e.stopPropagation(); openTicket(ticketId); }
          return;
        }
        var row = e.target.closest('[data-log-id]');
        if (row) toggleLogRow(row.getAttribute('data-log-id'));
      });
    }
  }

  function bindAnalytics() {
    var refresh = $('#insightRefresh');
    if (refresh) refresh.addEventListener('click', function () { loadAnalytics(true); });
    var link = $('#reportLink');
    if (link) link.href = '/reports/' + encodeURIComponent('历史工单分析报告.html');
  }

  function bindAbout() {}

  function init() {
    setDates();
    bindNav();
    bindGlobal();
    bindTickets();
    bindLogs();
    bindAnalytics();
    bindAbout();
    if (window.Demo) Demo.init();
    if (window.Charts) {
      var resizeTimer = null;
      window.addEventListener('resize', function () {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(function () { Charts.resizeAll(); }, 160);
      });
    }

    var hash = location.hash.replace('#', '');
    var page = PAGES[hash] ? hash : 'dashboard';
    switchPage(page, { initial: true, skipHistory: true });

    var query = new URLSearchParams(location.search);
    var ticketParam = query.get('ticket');
    if (ticketParam) {
      switchPage('tickets');
      setTimeout(function () { openTicket(ticketParam); }, 500);
    }

    checkHealth();
  }

  async function checkHealth() {
    try {
      await API.health();
      setApiStatus('ok', '服务正常');
    } catch (e) {
      setApiStatus(e.kind === 'http' ? 'off' : 'down', e.kind === 'http' ? '接口降级' : '服务未连接');
    }
  }

  window.App = {
    openTicket: openTicket,
    closeDrawer: closeDrawer,
    rememberTicket: rememberTicket,
    switchPage: switchPage,
    refresh: refreshCurrentPage
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
