/* ==========================================================================
   parking.js —— 停车管理页（#parking）
   - 数据：GET /api/parking/dashboard
   - 操作：POST /api/parking/entry、/exit、/sessions/{id}/pay
   - 接口未就绪时展示本地示例数据 + 「服务未就绪」提示与重试，绝不白屏
   - 金额统一保留 2 位小数，时长统一「X小时Y分」
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
  function fmtMoney(n) {
    var v = num(n);
    return v == null ? '—' : v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function fmtRate(n) {
    var v = num(n);
    return v == null ? '—' : v.toFixed(2);
  }
  function fmtDuration(minutes) {
    var v = num(minutes);
    if (v == null || v < 0) return '—';
    var total = Math.round(v);
    var h = Math.floor(total / 60);
    var m = total % 60;
    return h + '小时' + m + '分';
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
  function pad2(n) { return String(n).padStart(2, '0'); }
  function fmtDT(d) {
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) + ' ' +
      pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
  }
  function minsAgo(m) { return fmtDT(new Date(Date.now() - m * 60000)); }
  function rnd(seed) {
    var x = Math.sin(seed * 12.9898) * 43758.5453;
    return x - Math.floor(x);
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

  var STATUS = {
    parked: { label: '停放中', cls: 'parked' },
    unpaid: { label: '待缴费', cls: 'unpaid' },
    paid: { label: '已支付', cls: 'paid' }
  };

  function statusMeta(session) {
    var code = session.status || 'parked';
    var meta = STATUS[code] || { label: code, cls: 'parked' };
    return { label: session.status_label || meta.label, cls: meta.cls };
  }

  function paymentLabel(session) {
    if (session.payment_label && session.payment_label !== '—') return session.payment_label;
    if (session.payment_method) {
      var map = { wechat: '微信', alipay: '支付宝', cash: '现金', monthly: '月卡' };
      return map[session.payment_method] || session.payment_method;
    }
    return '—';
  }

  function parkedFee(session) {
    var estimated = num(session.estimated_fee);
    var fee = num(session.fee);
    if (estimated != null && estimated > 0) return estimated;
    if (fee != null) return fee;
    return 0;
  }

  /* ======================================================================
     二、本地示例数据（接口未就绪时降级展示）
     ====================================================================== */
  var mockCache = null;

  function buildMock() {
    var plates = ['粤B12345', '粤BD88K9', '粤BA9XKF', '粤BVH3WT', '粤B6T21Q', '粤B0K8P7', '粤BF3M20', '粤B9Q7ZL'];
    var types = ['小型车', '新能源', 'SUV', '小型车', '新能源', '小型车', 'SUV', '新能源'];
    var owners = ['李先生', '张女士', '武霞', '王建国', '陈美玲', '刘志远', '周玉梅', '许志强'];
    var locations = ['8栋2单元', '3栋1单元', '5栋3单元', '12栋2单元', '2栋1单元', '6栋2单元', '9栋1单元', '11栋3单元'];
    var parked = [];
    for (var i = 0; i < 8; i++) {
      var minutes = [348, 34, 94, 81, 512, 1460, 58, 220][i];
      var entryAt = minsAgo(minutes);
      var monthly = i === 3 || i === 5;
      parked.push({
        id: 'PS20261020' + String(i + 1).padStart(4, '0'),
        plate: plates[i],
        vehicle_type: types[i],
        owner_name: owners[i],
        location: locations[i],
        entry_at: entryAt,
        duration_minutes: minutes,
        fee: monthly ? 0 : Math.min(30, Math.ceil(Math.max(0, minutes - 30) / 60) * 2),
        estimated_fee: monthly ? 0 : Math.min(30, Math.ceil(Math.max(0, minutes - 30) / 60) * 2),
        status: 'parked',
        status_label: '停放中',
        payment_method: null,
        paid_at: null,
        is_monthly: monthly,
        space_no: 'B1-' + String(i + 12).padStart(3, '0'),
        is_overstay: minutes > 1440
      });
    }

    var recent = [];
    var recentPlates = ['粤B67890', '粤BD31L7', '粤B2F9C4', '粤B7H1N6', '粤BQ5R88', '粤B4X2S1', '粤BK9D07', '粤BT6W32', '粤B1M8V5', '粤B8C3Y9'];
    var recentOwners = ['张女士', '赵先生', '陈女士', '孙先生', '林女士', '何先生', '刘女士', '郑先生', '曹女士', '冯先生'];
    for (var j = 0; j < 10; j++) {
      var dur = [220, 96, 41, 305, 28, 152, 480, 63, 199, 88][j];
      var fee = j < 2 ? 0 : Math.min(30, Math.ceil(Math.max(0, dur - 30) / 60) * 2);
      var status = j < 2 ? 'unpaid' : 'paid';
      var method = status === 'paid' ? (j % 3 === 0 ? '微信' : j % 3 === 1 ? '支付宝' : '现金') : null;
      recent.push({
        id: 'PS20261020' + String(100 + j).padStart(4, '0'),
        plate: recentPlates[j],
        vehicle_type: j % 2 ? '新能源' : '小型车',
        owner_name: recentOwners[j],
        location: '临时车辆',
        entry_at: minsAgo(dur + 90 + j * 20),
        exit_at: minsAgo(60 + j * 16),
        duration_minutes: dur,
        fee: fee,
        estimated_fee: 0,
        status: status,
        status_label: status === 'paid' ? '已支付' : '待缴费',
        payment_method: method,
        paid_at: status === 'paid' ? minsAgo(55 + j * 16) : null,
        is_monthly: false,
        space_no: 'B1-' + String(30 + j).padStart(3, '0'),
        is_overstay: false
      });
    }

    var revenueTrend = [];
    for (var d = 13; d >= 0; d--) {
      var day = new Date();
      day.setDate(day.getDate() - d);
      revenueTrend.push({
        date: day.getFullYear() + '-' + pad2(day.getMonth() + 1) + '-' + pad2(day.getDate()),
        revenue: Math.round((172 + rnd(d + 3) * 62) * 100) / 100,
        sessions: 40 + Math.round(rnd(d + 9) * 10)
      });
    }
    var hourly = [];
    var hourProfile = [0, 0, 0, 0, 1, 5, 15, 11, 8, 11, 11, 11, 10, 6, 3, 2, 2, 3, 6, 8, 5, 3, 2, 1];
    for (var h = 0; h < 24; h++) hourly.push({ hour: h, entries: hourProfile[h] });

    var occupied = 42;
    var total = 120;
    return {
      generated_at: fmtDT(new Date()),
      lot: {
        name: '地下一层停车场', total_spaces: total, occupied: occupied,
        available: total - occupied, occupancy_rate: Math.round(occupied / total * 1000) / 10
      },
      stats: {
        today_revenue: 206.0, month_revenue: 2722.0,
        today_entries: 89, today_exits: 48, today_paid_sessions: 44,
        avg_duration_minutes: 164.4, monthly_pass: 68, temporary_pass: 72,
        unpaid_count: 2, unpaid_amount: recent.filter(function (s) { return s.status === 'unpaid'; })
          .reduce(function (sum, s) { return sum + s.fee; }, 0)
      },
      revenue_trend: revenueTrend,
      hourly_entries: hourly,
      payment_mix: [
        { name: '微信', value: 190 }, { name: '支付宝', value: 223 },
        { name: '月卡', value: 131 }, { name: '现金', value: 84 }
      ],
      parked: parked,
      recent_sessions: recent,
      rules: {
        free_minutes: 30, rate_per_hour: 2.0, daily_cap: 30.0, monthly_fee: 300.0,
        description: '30 分钟内免费；超出后 2 元/小时，单日封顶 30 元；月卡 300 元/月'
      }
    };
  }

  function getMock() {
    if (!mockCache) mockCache = buildMock();
    return mockCache;
  }

  function normalizeSession(raw) {
    raw = raw || {};
    return {
      id: String(raw.id || ''),
      plate: raw.plate || '—',
      vehicle_type: raw.vehicle_type || '小型车',
      owner_name: raw.owner_name || '临时车主',
      location: raw.location || '—',
      entry_at: raw.entry_at || null,
      exit_at: raw.exit_at || null,
      duration_minutes: num(raw.duration_minutes),
      fee: num(raw.fee),
      estimated_fee: num(raw.estimated_fee),
      status: raw.status || 'parked',
      status_label: raw.status_label || (STATUS[raw.status] ? STATUS[raw.status].label : raw.status || '—'),
      payment_method: raw.payment_method || null,
      payment_label: raw.payment_label || null,
      paid_at: raw.paid_at || null,
      is_monthly: !!raw.is_monthly,
      space_no: raw.space_no || '—',
      is_overstay: !!raw.is_overstay
    };
  }

  function normalizeDashboard(raw) {
    raw = raw || {};
    var lot = raw.lot || {};
    var rules = raw.rules || {};
    return {
      generated_at: raw.generated_at || '',
      lot: {
        name: lot.name || '地下一层停车场',
        total_spaces: num(lot.total_spaces) != null ? num(lot.total_spaces) : null,
        occupied: num(lot.occupied) != null ? num(lot.occupied) : null,
        available: num(lot.available) != null ? num(lot.available) : null,
        occupancy_rate: num(lot.occupancy_rate)
      },
      stats: raw.stats || {},
      revenue_trend: arr(raw.revenue_trend),
      hourly_entries: arr(raw.hourly_entries),
      payment_mix: arr(raw.payment_mix),
      parked: arr(raw.parked).map(normalizeSession),
      recent_sessions: arr(raw.recent_sessions).map(normalizeSession),
      rules: {
        free_minutes: num(rules.free_minutes),
        rate_per_hour: num(rules.rate_per_hour),
        daily_cap: num(rules.daily_cap),
        monthly_fee: num(rules.monthly_fee),
        description: rules.description || ''
      }
    };
  }

  /* ======================================================================
     三、页面状态
     ====================================================================== */
  var state = {
    loaded: false,
    loading: false,
    seq: 0,
    data: null,
    live: false,
    error: null,
    entryOpen: false,
    entrySubmitting: false,
    leavingPlate: null,
    unpaidSessions: [],
    lastPaid: [],
    pay: { session: null, billing: null, mode: 'exit', submitting: false }
  };

  /* ======================================================================
     四、数据加载
     ====================================================================== */
  async function loadDashboard(force, quiet) {
    if (state.loading) return;
    var seq = ++state.seq;
    state.loading = true;
    var refresh = $('#parkingRefresh');
    if (refresh && !quiet) refresh.classList.add('loading');
    var data = null, live = false, err = null;
    var unpaidRaw = null;
    try {
      var res = await Promise.all([
        API.parkingDashboard(),
        /* 待缴费记录单独查询：看板的最近记录只有 15 条，可能不含较早的待缴费记录 */
        API.parkingSessions({ status: 'unpaid', limit: 50 }).catch(function () { return null; })
      ]);
      var raw = res[0];
      unpaidRaw = res[1];
      if (!raw || typeof raw !== 'object') throw new Error('空响应');
      data = normalizeDashboard(raw);
      live = true;
    } catch (e) {
      err = e;
      if (state.data && state.live) {
        data = state.data;
        live = true;
      } else {
        data = normalizeDashboard(getMock());
        live = false;
      }
    }
    if (seq !== state.seq) return;
    state.data = data;
    state.live = live;
    state.error = err;
    state.unpaidSessions = live && unpaidRaw
      ? arr(unpaidRaw.items).map(normalizeSession)
      : (live ? state.unpaidSessions : []);
    state.loaded = true;
    state.loading = false;
    if (refresh) refresh.classList.remove('loading');

    renderSampleNote();
    renderAll();

    if (live) setApiStatus('ok', '服务正常');
    else if (err) setApiStatus(err.kind === 'http' ? 'off' : 'down', err.kind === 'http' ? '接口降级' : '服务未连接');
  }

  function sampleState() {
    return { sample: !state.live, error: state.error };
  }

  function renderSampleNote() {
    var el = $('#parkingSampleNote');
    if (!el) return;
    var st = sampleState();
    el.hidden = !st.sample;
    if (!st.sample) { el.innerHTML = ''; return; }
    var errText = st.error && window.API ? API.describeError(st.error) : '';
    el.innerHTML = icon('alert') +
      '<div><strong>服务未就绪 · 示例数据。</strong>' +
      esc(errText ? errText + '，当前展示本地示例停车场数据；接口恢复后自动切换真实数据。'
        : '停车管理接口暂不可用，当前展示本地示例数据；接口恢复后自动切换真实数据。') +
      '</div>' +
      '<div class="sample-note-actions"><button class="btn ghost sm" type="button" data-retry-page="parking">重试接口</button></div>';
  }

  /* ======================================================================
     五、KPI
     ====================================================================== */
  function setKpi(id, value, decimals) {
    var el = document.getElementById(id);
    if (el) countUp(el, value, decimals);
  }

  function renderKpis(data) {
    var s = data.stats || {};
    var lot = data.lot || {};
    setKpi('pKpiToday', s.today_revenue, 2);
    setKpi('pKpiMonth', s.month_revenue, 2);
    setKpi('pKpiParked', lot.occupied, 0);
    setKpi('pKpiFree', lot.available, 0);
    setKpi('pKpiOccupancy', lot.occupancy_rate, 1);
    setKpi('pKpiUnpaid', s.unpaid_amount, 2);

    var foots = {
      pKpiTodayFoot: '<span class="trend up">' + icon('check') + '缴费 ' + esc(fmtNum(s.today_paid_sessions)) + ' 笔</span>' +
        '<span class="trend-divider"></span><span>出场 ' + esc(fmtNum(s.today_exits)) + ' 辆</span>',
      pKpiMonthFoot: '<span class="trend flat">入场 ' + esc(fmtNum(s.today_entries)) + ' 辆今日</span>' +
        '<span class="trend-divider"></span><span>月卡 ' + esc(fmtNum(s.monthly_pass)) + ' 辆</span>',
      pKpiParkedFoot: '<span class="trend flat">平均停放 ' + esc(fmtDuration(s.avg_duration_minutes)) + '</span>',
      pKpiFreeFoot: '<span class="trend flat">总车位 ' + esc(fmtNum(lot.total_spaces)) + ' 个</span>',
      pKpiOccupancyFoot: '<span class="trend ' + ((num(lot.occupancy_rate) || 0) > 85 ? 'down' : 'up') + '">' +
        icon('car') + '在停 ' + esc(fmtNum(lot.occupied)) + ' / ' + esc(fmtNum(lot.total_spaces)) + '</span>',
      pKpiUnpaidFoot: '<span class="trend down">' + icon('alert') + '共 ' + esc(fmtNum(s.unpaid_count)) + ' 笔待缴费</span>'
    };
    Object.keys(foots).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.innerHTML = foots[id];
    });
  }

  /* ======================================================================
     六、图表
     ====================================================================== */
  function chartTooltip(extra) {
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

  function renderRevenueChart(list) {
    var el = $('#chartParkingRevenue');
    if (!window.Charts || !el) return;
    list = arr(list);
    if (!list.length) return Charts.empty(el, '暂无收入数据', '接口未返回 revenue_trend');
    var inst = Charts.init(el);
    if (!inst) return;
    var labels = list.map(function (d) { return String(d.date || '').slice(5); });
    var revenue = list.map(function (d) { return num(d.revenue) || 0; });
    var sessions = list.map(function (d) { return num(d.sessions) || 0; });
    inst.setOption({
      animationDuration: 650,
      tooltip: chartTooltip({
        trigger: 'axis',
        axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(37,99,235,.05)' } },
        formatter: function (params) {
          if (!params || !params.length) return '';
          var idx = params[0].dataIndex;
          return esc(params[0].name) + '<br/>收入：<b>¥' + fmtMoney(revenue[idx]) + '</b>' +
            '<br/>计费订单：<b>' + sessions[idx] + '</b> 笔';
        }
      }),
      legend: {
        top: 0, right: 0, icon: 'roundRect', itemWidth: 9, itemHeight: 9, itemGap: 14,
        textStyle: { color: '#4b5f7a', fontSize: 10.5 }
      },
      grid: { left: 6, right: 10, top: 34, bottom: 0, containLabel: true },
      xAxis: {
        type: 'category', data: labels, boundaryGap: true,
        axisLine: { lineStyle: { color: '#e3ebf5' } }, axisTick: { show: false },
        axisLabel: { color: '#8496ad', fontSize: 9.5, interval: 1 }
      },
      yAxis: [
        {
          type: 'value', name: '收入 / 元', nameTextStyle: { color: '#8496ad', fontSize: 9.5 },
          axisLine: { show: false }, axisTick: { show: false },
          axisLabel: { color: '#8496ad', fontSize: 10, formatter: '¥{value}' },
          splitLine: { lineStyle: { color: '#e8eef7', type: 'dashed' } }
        },
        {
          type: 'value', name: '订单', minInterval: 1, nameTextStyle: { color: '#8496ad', fontSize: 9.5 },
          axisLine: { show: false }, axisTick: { show: false },
          axisLabel: { color: '#8496ad', fontSize: 10 }, splitLine: { show: false }
        }
      ],
      series: [
        {
          name: '收入', type: 'bar', barMaxWidth: 18, barWidth: '44%',
          itemStyle: { borderRadius: [4, 4, 0, 0], color: '#16a34a' },
          data: revenue
        },
        {
          name: '计费订单', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'circle', symbolSize: 5,
          lineStyle: { width: 2.2, color: '#2563eb' },
          itemStyle: { color: '#2563eb', borderWidth: 2, borderColor: '#fff' },
          data: sessions
        }
      ]
    }, true);
  }

  function renderPaymentChart(list) {
    var el = $('#chartParkingPayment');
    if (!window.Charts || !el) return;
    var merged = {};
    arr(list).forEach(function (d) {
      var name = d.name || '其他';
      var value = num(d.value) || 0;
      if (value <= 0) return;
      merged[name] = (merged[name] || 0) + value;
    });
    var data = Object.keys(merged).map(function (name, i) {
      return { name: name, value: merged[name], itemStyle: { color: ['#16a34a', '#2563eb', '#8b5cf6', '#f59e0b', '#06b6d4'][i % 5] } };
    });
    if (!data.length) return Charts.empty(el, '暂无支付数据', '接口未返回 payment_mix');
    var inst = Charts.init(el);
    if (!inst) return;
    var total = data.reduce(function (sum, d) { return sum + d.value; }, 0);
    inst.setOption({
      animationDuration: 650,
      tooltip: chartTooltip({ trigger: 'item', formatter: '{b}<br/><b>{c}</b> 笔 · {d}%' }),
      legend: {
        bottom: 0, left: 'center', icon: 'circle', itemWidth: 8, itemHeight: 8, itemGap: 12,
        textStyle: { color: '#4b5f7a', fontSize: 10.5 }
      },
      title: {
        text: fmtNum(total), subtext: '缴费笔数', left: '50%', top: '33%', textAlign: 'center',
        textStyle: { fontSize: 21, fontWeight: 700, color: '#0f2544' },
        subtextStyle: { fontSize: 9.5, color: '#8496ad' }
      },
      series: [{
        name: '支付方式', type: 'pie', radius: ['45%', '64%'], center: ['50%', '44%'],
        itemStyle: { borderColor: '#fff', borderWidth: 2, borderRadius: 4 },
        label: { show: false }, labelLine: { show: false },
        emphasis: { scale: true, scaleSize: 4, itemStyle: { shadowBlur: 12, shadowColor: 'rgba(15,37,68,.18)' } },
        data: data
      }]
    }, true);
  }

  function renderHourlyChart(list) {
    var el = $('#chartParkingHourly');
    if (!window.Charts || !el) return;
    list = arr(list);
    if (!list.length) return Charts.empty(el, '暂无入场数据', '接口未返回 hourly_entries');
    var inst = Charts.init(el);
    if (!inst) return;
    var byHour = {};
    list.forEach(function (d) { byHour[num(d.hour)] = num(d.entries) || 0; });
    var labels = [], values = [];
    for (var h = 0; h < 24; h++) {
      labels.push(h + '时');
      values.push(byHour[h] || 0);
    }
    inst.setOption({
      animationDuration: 650,
      tooltip: chartTooltip({
        trigger: 'axis',
        axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(37,99,235,.05)' } },
        formatter: function (params) {
          if (!params || !params.length) return '';
          return params[0].name + ' 入场：<b>' + params[0].value + '</b> 辆';
        }
      }),
      grid: { left: 6, right: 10, top: 12, bottom: 0, containLabel: true },
      xAxis: {
        type: 'category', data: labels,
        axisLine: { lineStyle: { color: '#e3ebf5' } }, axisTick: { show: false },
        axisLabel: { color: '#8496ad', fontSize: 9.5, interval: 3 }
      },
      yAxis: {
        type: 'value', minInterval: 1,
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: '#8496ad', fontSize: 10 },
        splitLine: { lineStyle: { color: '#e8eef7', type: 'dashed' } }
      },
      series: [{
        type: 'bar', barMaxWidth: 16, barWidth: '56%',
        itemStyle: { borderRadius: [4, 4, 0, 0], color: '#06b6d4' },
        data: values
      }]
    }, true);
  }

  function renderOccupancyChart(lot) {
    var el = $('#chartParkingOccupancy');
    if (!window.Charts || !el) return;
    var rate = num(lot && lot.occupancy_rate);
    if (rate == null) return Charts.empty(el, '暂无车位数据', '接口未返回 lot.occupancy_rate');
    var inst = Charts.init(el);
    if (!inst) return;
    var color = rate >= 85 ? '#ef4444' : rate >= 60 ? '#f59e0b' : '#2563eb';
    inst.setOption({
      animationDuration: 800,
      series: [{
        type: 'gauge', startAngle: 210, endAngle: -30, radius: '92%', center: ['50%', '58%'],
        min: 0, max: 100,
        progress: { show: true, width: 15, itemStyle: { color: color } },
        axisLine: { lineStyle: { width: 15, color: [[1, '#eef3fa']] } },
        axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
        pointer: { show: false }, anchor: { show: false },
        detail: {
          valueAnimation: true, formatter: '{value}%', offsetCenter: [0, '6%'],
          fontSize: 27, fontWeight: 700, color: '#0f2544'
        },
        title: { show: true, offsetCenter: [0, '38%'], fontSize: 11, color: '#8496ad' },
        data: [{ value: Math.round(rate * 10) / 10, name: '车位占用率' }]
      }]
    }, true);
  }

  /* ======================================================================
     七、表格 / 规则
     ====================================================================== */
  function emptyRow(colspan, title, desc, retry) {
    return '<tr><td colspan="' + colspan + '">' +
      (window.UI && UI.emptyHTML
        ? UI.emptyHTML(title, desc, 'car', retry ? '<button class="btn ghost sm" type="button" data-retry-page="parking">重试接口</button>' : '')
        : '<div class="recent-empty">' + esc(desc) + '</div>') +
      '</td></tr>';
  }

  function renderParkedTable(data) {
    var tbody = $('#parkingRows');
    var meta = $('#parkingParkedMeta');
    if (!tbody) return;
    var list = arr(data.parked);
    var occupied = num(data.lot && data.lot.occupied);
    if (meta) {
      meta.textContent = occupied != null && occupied > list.length
        ? '在停 ' + fmtNum(occupied) + ' 辆 · 显示前 ' + fmtNum(list.length) + ' 辆'
        : '在停 ' + fmtNum(list.length) + ' 辆';
    }
    if (!list.length) {
      tbody.innerHTML = emptyRow(8, state.live ? '暂无在停车辆' : '服务未就绪',
        state.live ? '当前停车场没有在停车辆，可通过「模拟车辆入场」添加测试车辆。' : '停车看板接口未就绪，请启动后端服务后重试。', !state.live);
      return;
    }
    tbody.innerHTML = list.map(function (s) {
      var fee = parkedFee(s);
      var leaving = state.leavingPlate === s.plate;
      return '<tr data-parked-plate="' + esc(s.plate) + '">' +
        '<td><div class="plate-cell"><strong class="plate">' + esc(s.plate) + '</strong>' +
        (s.is_monthly ? '<span class="monthly-pill">' + icon('check') + '月卡</span>' : '') +
        (s.is_overstay ? '<span class="overstay-pill">超时</span>' : '') + '</div></td>' +
        '<td>' + esc(s.vehicle_type) + '</td>' +
        '<td><div class="owner-cell"><strong>' + esc(s.owner_name) + '</strong><span>' + esc(s.location) + '</span></div></td>' +
        '<td class="cell-time">' + esc(fullTime(s.entry_at)) + '</td>' +
        '<td class="num">' + esc(fmtDuration(s.duration_minutes)) + '</td>' +
        '<td class="num"><span class="money">¥' + esc(fmtMoney(s.is_monthly ? 0 : fee)) + '</span></td>' +
        '<td><span class="space-no">' + esc(s.space_no) + '</span></td>' +
        '<td class="num"><div class="row-actions">' +
        '<button class="btn ghost sm" type="button" data-exit-plate="' + esc(s.plate) + '"' + (leaving ? ' disabled' : '') + '>' +
        (leaving ? '出场中…' : '模拟出场') + '</button></div></td>' +
        '</tr>';
    }).join('');
  }

  function renderRecentTable(data) {
    var tbody = $('#parkingRecentRows');
    var meta = $('#parkingRecentMeta');
    if (!tbody) return;
    /* 待缴费记录优先展示，确保「收费」入口可见 */
    var merged = [];
    var seen = {};
    arr(state.unpaidSessions).forEach(function (s) {
      if (!s.id || seen[s.id]) return;
      seen[s.id] = 1;
      merged.push(s);
    });
    /* 刚完成收费的记录保留在列表中，给出可见的收款结果 */
    arr(state.lastPaid).forEach(function (s) {
      if (!s.id || seen[s.id]) return;
      seen[s.id] = 1;
      merged.push(s);
    });
    arr(data.recent_sessions).forEach(function (s) {
      if (!s.id || seen[s.id]) return;
      seen[s.id] = 1;
      merged.push(s);
    });
    var list = merged.slice(0, 20);
    var unpaidVisible = list.filter(function (s) { return s.status === 'unpaid'; }).length;
    if (meta) {
      meta.textContent = (unpaidVisible ? '待缴费 ' + fmtNum(unpaidVisible) + ' 条 · ' : '') + '最近 ' + fmtNum(list.length) + ' 条';
    }
    if (!list.length) {
      tbody.innerHTML = emptyRow(8, state.live ? '暂无进出记录' : '服务未就绪',
        state.live ? '暂无车辆进出记录，可通过「模拟车辆入场」生成测试数据。' : '停车看板接口未就绪，请启动后端服务后重试。', !state.live);
      return;
    }
    tbody.innerHTML = list.map(function (s) {
      var sm = statusMeta(s);
      var action = '—';
      if (s.status === 'unpaid') {
        action = '<button class="btn primary sm" type="button" data-pay-session="' + esc(s.id) + '">收费</button>';
      } else if (s.status === 'parked') {
        action = '<button class="btn ghost sm" type="button" data-exit-plate="' + esc(s.plate) + '">模拟出场</button>';
      }
      return '<tr data-session-id="' + esc(s.id) + '">' +
        '<td><div class="plate-cell"><strong class="plate">' + esc(s.plate) + '</strong>' +
        (s.is_monthly ? '<span class="monthly-pill">月卡</span>' : '') + '</div></td>' +
        '<td class="cell-time">' + esc(shortTime(s.entry_at)) + '</td>' +
        '<td class="cell-time">' + esc(shortTime(s.exit_at)) + '</td>' +
        '<td class="num">' + esc(fmtDuration(s.duration_minutes)) + '</td>' +
        '<td class="num"><span class="money">¥' + esc(fmtMoney(s.status === 'parked' ? parkedFee(s) : s.fee)) + '</span></td>' +
        '<td><span class="parking-status ' + sm.cls + '">' + esc(sm.label) + '</span></td>' +
        '<td>' + esc(paymentLabel(s)) + '</td>' +
        '<td class="num"><div class="row-actions">' + action + '</div></td>' +
        '</tr>';
    }).join('');
  }

  function renderRules(rules) {
    var wrap = $('#parkingRules');
    var desc = $('#parkingRulesDesc');
    if (!wrap) return;
    rules = rules || {};
    var tiles = [
      { key: '免费时长', value: fmtNum(rules.free_minutes) + ' 分钟', note: '进场即开始计时' },
      { key: '计费单价', value: '¥' + fmtRate(rules.rate_per_hour) + ' / 小时', note: '不足 1 小时按 1 小时计' },
      { key: '单日封顶', value: '¥' + fmtRate(rules.daily_cap), note: '超过封顶按封顶收取' },
      { key: '月卡', value: '¥' + fmtRate(rules.monthly_fee) + ' / 月', note: '月卡车辆出场不另收费' }
    ];
    wrap.innerHTML = tiles.map(function (t) {
      return '<div class="rule-tile"><span>' + esc(t.key) + '</span><strong>' + esc(t.value) + '</strong>' +
        '<small>' + esc(t.note) + '</small></div>';
    }).join('');
    if (desc) desc.textContent = rules.description ? '计费口径：' + rules.description : '';
  }

  function renderLotInfo(lot) {
    var name = $('#parkingLotName');
    var badge = $('#parkingLotBadge');
    var info = $('#parkingLotInfo');
    if (name) name.textContent = lot.name || '停车场';
    if (badge) badge.textContent = '占用 ' + (num(lot.occupancy_rate) == null ? '—' : fmtNum(lot.occupancy_rate) + '%');
    if (info) {
      info.textContent = '在停 ' + fmtNum(lot.occupied) + ' 辆 · 空余 ' + fmtNum(lot.available) +
        ' 个 · 总车位 ' + fmtNum(lot.total_spaces) + ' 个';
    }
  }

  function resizeCharts() {
    var pageEl = document.getElementById('page-parking');
    if (pageEl && window.Charts) Charts.resizeIn(pageEl);
  }

  function renderAll() {
    if (!state.data) return;
    var data = state.data;
    renderKpis(data);
    renderRevenueChart(data.revenue_trend);
    renderPaymentChart(data.payment_mix);
    renderHourlyChart(data.hourly_entries);
    renderOccupancyChart(data.lot);
    renderLotInfo(data.lot);
    renderParkedTable(data);
    renderRecentTable(data);
    renderRules(data.rules);
    requestAnimationFrame(resizeCharts);
  }

  /* ======================================================================
     八、模拟入场
     ====================================================================== */
  function setEntryOpen(open) {
    var form = $('#parkingEntryForm');
    var toggle = $('#parkingEntryToggle');
    state.entryOpen = !!open;
    if (form) form.hidden = !open;
    if (toggle) toggle.classList.toggle('active', !!open);
    setEntryError('');
    if (open) {
      setTimeout(function () {
        var input = $('#parkingPlate');
        if (input) input.focus();
      }, 60);
    }
  }

  function setEntryError(text) {
    var el = $('#parkingEntryError');
    if (!el) return;
    el.hidden = !text;
    el.textContent = text || '';
  }

  function setEntryLoading(loading) {
    state.entrySubmitting = loading;
    var btn = $('#parkingEntrySubmit');
    if (!btn) return;
    btn.disabled = loading;
    btn.classList.toggle('loading', loading);
  }

  async function submitEntry() {
    if (state.entrySubmitting) return;
    var plateEl = $('#parkingPlate');
    var typeEl = $('#parkingVehicleType');
    var plate = plateEl ? plateEl.value.trim().toUpperCase() : '';
    var vehicleType = typeEl ? typeEl.value : '小型车';
    if (!plate) {
      setEntryError('请输入车牌号，例如 粤B12345');
      if (plateEl) plateEl.focus();
      return;
    }
    if (plate.length < 4) {
      setEntryError('车牌号格式不正确，请检查后重试');
      if (plateEl) plateEl.focus();
      return;
    }
    if (!state.live) {
      setEntryError('服务未就绪，无法模拟车辆入场；请启动后端服务后重试。');
      toast('服务未就绪，入场操作未执行', 'warn');
      return;
    }
    setEntryError('');
    setEntryLoading(true);
    try {
      var res = await API.parkingEntry({ plate: plate, vehicle_type: vehicleType });
      var session = (res && res.session) || {};
      var note = res && res.billing_note ? '；' + res.billing_note : '';
      toast('车辆 ' + plate + ' 已入场' + (session.space_no ? '，分配车位 ' + session.space_no : '') + note, 'success');
      if (plateEl) plateEl.value = '';
      setEntryOpen(false);
      await loadDashboard(true, true);
    } catch (e) {
      var text = window.API ? API.describeError(e) : '入场失败';
      setEntryError('入场失败：' + text);
      toast('入场失败：' + text, 'error');
    } finally {
      setEntryLoading(false);
    }
  }

  /* ======================================================================
     九、模拟出场 / 收费
     ====================================================================== */
  async function simulateExit(plate, btn) {
    if (!plate || state.leavingPlate) return;
    if (!state.live) {
      toast('当前为示例数据，模拟出场需要后端服务可用', 'warn');
      return;
    }
    state.leavingPlate = plate;
    if (btn) { btn.disabled = true; btn.classList.add('loading'); }
    try {
      var res = await API.parkingExit({ plate: plate });
      var session = normalizeSession((res && res.session) || {});
      var billing = (res && res.billing) || null;
      var fee = billing && num(billing.fee) != null ? num(billing.fee) : (num(session.fee) || 0);
      var monthly = (billing && billing.is_monthly) || session.is_monthly;
      await loadDashboard(true, true);
      if (session.status === 'paid' || monthly) {
        toast('车辆 ' + plate + ' 已出场' + (monthly ? '（月卡车辆，不另收费）' : '，无需缴费'), 'success');
        if (monthly) {
          var last = (state.data && state.data.recent_sessions || []).filter(function (s) { return s.plate === plate; })[0];
          if (last) toast('出场记录 ' + last.id + ' · ' + fmtDuration(last.duration_minutes), 'info');
        }
      } else if (fee <= 0) {
        /* 30 分钟内免费：自动结算 0 元，避免留下 0 元待缴费记录 */
        try {
          await API.parkingPay(session.id, { payment_method: '现金' });
          toast('车辆 ' + plate + ' 已出场，停车时长 ' + fmtDuration(session.duration_minutes) + '（30 分钟内免费），无需缴费', 'success');
        } catch (payErr) {
          toast('车辆 ' + plate + ' 已出场，未产生费用', 'success');
        }
        await loadDashboard(true, true);
      } else {
        toast('车辆 ' + plate + ' 已出场，待缴费 ¥' + fmtMoney(fee), 'info');
        openPayModal(session, billing, 'exit');
      }
    } catch (e) {
      toast('出场失败：' + (window.API ? API.describeError(e) : '接口未就绪'), 'error');
    } finally {
      state.leavingPlate = null;
      if (btn && btn.parentNode) { btn.disabled = false; btn.classList.remove('loading'); }
    }
  }

  function openPayModal(session, billing, mode) {
    var overlay = $('#parkingPayOverlay');
    if (!overlay) return;
    state.pay.session = session || null;
    state.pay.billing = billing || null;
    state.pay.mode = mode || 'exit';
    state.pay.submitting = false;
    setPayError('');

    var summary = $('#parkingPaySummary');
    var title = $('#parkingPayTitle');
    var fee = billing && num(billing.fee) != null ? num(billing.fee) : (session ? num(session.fee) || 0 : 0);
    var monthly = (billing && billing.is_monthly) || (session && session.is_monthly);
    if (title) title.textContent = (mode === 'exit' ? '车辆出场收费' : '停车费缴纳') +
      (session && session.plate ? ' · ' + session.plate : '');

    if (summary) {
      var lines = [];
      if (session) {
        lines.push(['车牌号', session.plate]);
        if (session.space_no) lines.push(['车位号', session.space_no]);
        if (session.entry_at) lines.push(['入场时间', fullTime(session.entry_at)]);
        if (session.exit_at) lines.push(['出场时间', fullTime(session.exit_at)]);
        lines.push(['停车时长', session.duration_minutes != null ? fmtDuration(session.duration_minutes) : '—']);
      }
      var breakdown = billing && billing.breakdown ? billing.breakdown : '';
      summary.innerHTML =
        '<div class="pay-fields">' + lines.map(function (row) {
          return '<div class="pay-field"><span>' + esc(row[0]) + '</span><strong>' + esc(row[1]) + '</strong></div>';
        }).join('') + '</div>' +
        '<div class="pay-amount-row"><div><span>应收金额</span>' +
        '<strong class="pay-amount">' + (monthly ? '月卡 · 不另收费' : '¥' + fmtMoney(fee)) + '</strong></div>' +
        (billing && billing.billable_hours != null && !monthly
          ? '<span class="pay-billable">计费 ' + esc(fmtNum(billing.billable_hours)) + ' 小时 · 免费 ' +
          esc(fmtNum(billing.free_minutes)) + ' 分钟</span>' : '') +
        '</div>' +
        (breakdown ? '<div class="pay-breakdown">' + icon('info') + esc(breakdown) + '</div>' : '') +
        '<div class="pay-methods-hint">选择支付方式完成收费（微信 / 支付宝 / 现金）</div>';
    }

    var methods = $('#parkingPayMethods');
    if (methods) {
      var disabled = monthly || fee <= 0;
      methods.classList.toggle('disabled', !!disabled);
      $$('.pay-method', methods).forEach(function (btn) {
        btn.disabled = !!disabled || state.pay.submitting;
      });
    }
    overlay.hidden = false;
    requestAnimationFrame(function () { overlay.classList.add('open'); });
    setTimeout(function () {
      var first = $('#parkingPayMethods .pay-method:not([disabled])');
      if (first) first.focus();
    }, 80);
  }

  function closePayModal() {
    var overlay = $('#parkingPayOverlay');
    if (!overlay || overlay.hidden) return;
    overlay.classList.remove('open');
    setTimeout(function () { overlay.hidden = true; }, 160);
    state.pay.session = null;
    state.pay.billing = null;
    state.pay.submitting = false;
    setPayError('');
  }

  function setPayError(text) {
    var el = $('#parkingPayError');
    if (!el) return;
    el.hidden = !text;
    el.textContent = text || '';
  }

  async function payWith(method) {
    if (state.pay.submitting) return;
    var session = state.pay.session;
    if (!session || !session.id) {
      setPayError('缺少停车记录，无法收费');
      return;
    }
    if (!state.live) {
      setPayError('服务未就绪，无法完成收费');
      return;
    }
    state.pay.submitting = true;
    setPayError('');
    $$('#parkingPayMethods .pay-method').forEach(function (btn) { btn.disabled = true; });
    var fee = state.pay.billing && num(state.pay.billing.fee) != null
      ? num(state.pay.billing.fee) : (num(session.fee) || 0);
    try {
      var payRes = await API.parkingPay(session.id, { payment_method: method });
      var paidSession = normalizeSession((payRes && payRes.session) || session);
      paidSession.status = 'paid';
      paidSession.status_label = '已支付';
      if (!paidSession.payment_method) paidSession.payment_method = method;
      state.lastPaid = [paidSession].concat(state.lastPaid.filter(function (x) { return x.id !== paidSession.id; })).slice(0, 3);
      toast('已收款 ¥' + fmtMoney(fee) + '（' + method + '）' + ' · ' + session.plate, 'success');
      closePayModal();
      await loadDashboard(true, true);
    } catch (e) {
      setPayError('收费失败：' + (window.API ? API.describeError(e) : '接口未就绪'));
      toast('收费失败：' + (window.API ? API.describeError(e) : '接口未就绪'), 'error');
      $$('#parkingPayMethods .pay-method').forEach(function (btn) { btn.disabled = false; });
    } finally {
      state.pay.submitting = false;
    }
  }

  function payFromSession(sessionId, btn) {
    var session = null;
    var pools = [arr(state.unpaidSessions), arr(state.data && state.data.recent_sessions)];
    pools.some(function (pool) {
      return pool.some(function (s) {
        if (s.id === sessionId) { session = s; return true; }
        return false;
      });
    });
    if (!session) {
      toast('未找到该停车记录，请刷新后重试', 'warn');
      return;
    }
    if (btn) btn.disabled = true;
    openPayModal(session, null, 'pay');
    if (btn) btn.disabled = false;
  }

  /* ======================================================================
     十、事件绑定
     ====================================================================== */
  function bind() {
    var refresh = $('#parkingRefresh');
    if (refresh) refresh.addEventListener('click', function () { loadDashboard(true); });

    var toggle = $('#parkingEntryToggle');
    if (toggle) toggle.addEventListener('click', function () { setEntryOpen(!state.entryOpen); });
    var cancel = $('#parkingEntryCancel');
    if (cancel) cancel.addEventListener('click', function () { setEntryOpen(false); });
    var submit = $('#parkingEntrySubmit');
    if (submit) submit.addEventListener('click', submitEntry);
    var plate = $('#parkingPlate');
    if (plate) {
      plate.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); submitEntry(); }
      });
    }

    var parkedBody = $('#parkingRows');
    if (parkedBody) {
      parkedBody.addEventListener('click', function (e) {
        var btn = e.target.closest('[data-exit-plate]');
        if (!btn) return;
        e.preventDefault();
        simulateExit(btn.getAttribute('data-exit-plate'), btn);
      });
    }
    var recentBody = $('#parkingRecentRows');
    if (recentBody) {
      recentBody.addEventListener('click', function (e) {
        var payBtn = e.target.closest('[data-pay-session]');
        if (payBtn) {
          e.preventDefault();
          payFromSession(payBtn.getAttribute('data-pay-session'), payBtn);
          return;
        }
        var exitBtn = e.target.closest('[data-exit-plate]');
        if (exitBtn) {
          e.preventDefault();
          simulateExit(exitBtn.getAttribute('data-exit-plate'), exitBtn);
        }
      });
    }

    var methods = $('#parkingPayMethods');
    if (methods) {
      methods.addEventListener('click', function (e) {
        var btn = e.target.closest('[data-pay-method]');
        if (!btn || btn.disabled) return;
        payWith(btn.getAttribute('data-pay-method'));
      });
    }
    var payClose = $('#parkingPayClose');
    if (payClose) payClose.addEventListener('click', closePayModal);
    var payLater = $('#parkingPayLater');
    if (payLater) {
      payLater.addEventListener('click', function () {
        closePayModal();
        toast('已保留待缴费记录，可在「最近进出记录」中继续收费', 'info');
      });
    }
    var overlay = $('#parkingPayOverlay');
    if (overlay) {
      overlay.addEventListener('click', function (e) {
        if (e.target === overlay) closePayModal();
      });
    }
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      var ov = $('#parkingPayOverlay');
      if (ov && !ov.hidden) closePayModal();
    });
  }

  /* ======================================================================
     十一、对外接口
     ====================================================================== */
  function enter(force) {
    if (!state.loaded || force) {
      loadDashboard(force);
    } else {
      requestAnimationFrame(resizeCharts);
    }
  }

  function leave() {
    if (state.entryOpen) setEntryOpen(false);
    closePayModal();
    state.lastPaid = [];
    if (state.data) requestAnimationFrame(resizeCharts);
  }

  function init() {
    if (!$('#page-parking')) return;
    bind();
    bindResizeOnNav();
  }

  function bindResizeOnNav() {
    /* 首屏直接进入停车管理时，等布局稳定后再自适应一次图表 */
    setTimeout(function () {
      var page = document.getElementById('page-parking');
      if (page && page.classList.contains('active')) resizeCharts();
    }, 120);
  }

  window.ParkingUI = {
    init: init,
    enter: enter,
    leave: leave,
    refresh: function () { return loadDashboard(true); },
    state: state
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
