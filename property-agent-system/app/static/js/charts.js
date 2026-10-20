/* ==========================================================================
   charts.js —— ECharts 统一主题与图表封装
   所有图表实例集中登记，页面切换 / 窗口 resize 时统一自适应
   ========================================================================== */
(function () {
  'use strict';

  var registry = new Map();

  var PALETTE = ['#2563eb', '#06b6d4', '#16a34a', '#f59e0b', '#ef4444', '#8b5cf6', '#0ea5e9', '#14b8a6', '#f97316', '#64748b'];
  var TEXT = '#4b5f7a';
  var MUTED = '#8496ad';
  var GRID_LINE = '#e8eef7';
  var AXIS_LINE = '#e3ebf5';
  var FONT = '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif';

  function esc(str) {
    return String(str == null ? '' : str).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function fmtNum(n) {
    if (n == null || isNaN(n)) return '0';
    return Number(n).toLocaleString('zh-CN');
  }

  function ensure(el) {
    if (!el) return null;
    if (!window.echarts) {
      el.innerHTML = '<div class="chart-empty"><strong>图表库未加载</strong><span>请确认 /static/vendor/echarts.min.js 可访问</span></div>';
      return null;
    }
    var inst = null;
    try { inst = echarts.getInstanceByDom(el); } catch (e) { inst = null; }
    if (!inst) {
      el.innerHTML = '';
      try {
        inst = echarts.init(el, null, { renderer: 'canvas' });
      } catch (e) {
        return null;
      }
    }
    registry.set(el, inst);
    return inst;
  }

  function empty(el, title, desc) {
    if (!el) return;
    var inst = null;
    try { inst = echarts.getInstanceByDom(el); } catch (e) { inst = null; }
    if (inst) inst.dispose();
    registry.delete(el);
    el.innerHTML = '<div class="chart-empty">' +
      '<svg class="icon"><use href="#i-info"></use></svg>' +
      '<strong>' + esc(title || '暂无数据') + '</strong>' +
      '<span>' + esc(desc || '接口暂无数据返回') + '</span>' +
      '</div>';
  }

  function resizeAll() {
    registry.forEach(function (inst, el) {
      if (!el || !document.body.contains(el)) {
        registry.delete(el);
        return;
      }
      if (el.offsetParent !== null && el.clientWidth > 0) {
        try { inst.resize(); } catch (e) { /* ignore */ }
      }
    });
  }

  function resizeIn(root) {
    if (!root) return;
    registry.forEach(function (inst, el) {
      if (root.contains(el) && el.offsetParent !== null && el.clientWidth > 0) {
        try { inst.resize(); } catch (e) { /* ignore */ }
      }
    });
  }

  function linearGradient(from, to, horizontal) {
    if (!window.echarts) return to;
    return new echarts.graphic.LinearGradient(
      horizontal ? 0 : 0, horizontal ? 0 : 0,
      horizontal ? 1 : 0, horizontal ? 0 : 1,
      [{ offset: 0, color: from }, { offset: 1, color: to }]
    );
  }

  function tooltipBase(extra) {
    var base = {
      backgroundColor: 'rgba(11,31,58,0.94)',
      borderWidth: 0,
      padding: [9, 12],
      textStyle: { color: '#e6f0fb', fontSize: 11.5, fontFamily: FONT },
      extraCssText: 'border-radius:9px;box-shadow:0 14px 30px -16px rgba(0,0,0,.55);'
    };
    Object.keys(extra || {}).forEach(function (k) { base[k] = extra[k]; });
    return base;
  }

  function valueAxis(extra) {
    var base = {
      type: 'value',
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: MUTED, fontSize: 10, fontFamily: FONT },
      splitLine: { lineStyle: { color: GRID_LINE, type: 'dashed' } }
    };
    Object.keys(extra || {}).forEach(function (k) { base[k] = extra[k]; });
    return base;
  }

  function categoryAxis(data, extra) {
    var base = {
      type: 'category',
      data: data,
      axisLine: { lineStyle: { color: AXIS_LINE } },
      axisTick: { show: false },
      axisLabel: { color: MUTED, fontSize: 10, fontFamily: FONT }
    };
    Object.keys(extra || {}).forEach(function (k) { base[k] = extra[k]; });
    return base;
  }

  function legendBase(extra) {
    var base = {
      top: 0,
      right: 0,
      icon: 'roundRect',
      itemWidth: 9,
      itemHeight: 9,
      itemGap: 14,
      textStyle: { color: TEXT, fontSize: 10.5, fontFamily: FONT }
    };
    Object.keys(extra || {}).forEach(function (k) { base[k] = extra[k]; });
    return base;
  }

  /* --------------------------------- 总览 --------------------------------- */

  function trend(el, list) {
    list = Array.isArray(list) ? list : [];
    if (!list.length) return empty(el, '暂无趋势数据', '接口返回的趋势数组为空');
    var inst = ensure(el);
    if (!inst) return;
    var labels = list.map(function (d) { return String(d.date || '').slice(5); });
    var orders = list.map(function (d) { return Number(d.orders) || 0; });
    var closed = list.map(function (d) { return Number(d.closed) || 0; });
    var timeout = list.map(function (d) { return Number(d.timeout) || 0; });
    var interval = Math.max(0, Math.ceil(list.length / 8) - 1);

    inst.setOption({
      animationDuration: 650,
      color: PALETTE,
      tooltip: tooltipBase({ trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(37,99,235,.05)' } } }),
      legend: legendBase({ data: ['工单量', '闭环量', '超时量'] }),
      grid: { left: 6, right: 10, top: 34, bottom: 0, containLabel: true },
      xAxis: categoryAxis(labels, {
        boundaryGap: true,
        axisLabel: { color: MUTED, fontSize: 10, fontFamily: FONT, interval: interval, margin: 12 },
        axisLine: { lineStyle: { color: AXIS_LINE } }
      }),
      yAxis: valueAxis({ minInterval: 1 }),
      series: [
        {
          name: '工单量', type: 'bar', barMaxWidth: 18, barWidth: '42%',
          itemStyle: { borderRadius: [4, 4, 0, 0], color: linearGradient('#3b82f6', '#2563eb') },
          emphasis: { itemStyle: { color: linearGradient('#60a5fa', '#2563eb') } },
          data: orders
        },
        {
          name: '闭环量', type: 'line', smooth: true, symbol: 'circle', symbolSize: 5, showSymbol: false,
          lineStyle: { width: 2, color: '#16a34a' },
          itemStyle: { color: '#16a34a', borderWidth: 2, borderColor: '#fff' },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(22,163,74,.14)' },
              { offset: 1, color: 'rgba(22,163,74,0)' }
            ])
          },
          data: closed
        },
        {
          name: '超时量', type: 'line', smooth: true, symbol: 'circle', symbolSize: 5, showSymbol: false,
          lineStyle: { width: 2, color: '#ef4444', type: 'dashed' },
          itemStyle: { color: '#ef4444', borderWidth: 2, borderColor: '#fff' },
          data: timeout
        }
      ]
    }, true);
  }

  function typesDonut(el, list, totalValue) {
    list = Array.isArray(list) ? list : [];
    list = list.filter(function (d) { return Number(d.value) > 0; });
    if (!list.length) return empty(el, '暂无类型分布数据', '接口未返回报修类型统计');
    var inst = ensure(el);
    if (!inst) return;
    var sum = list.reduce(function (s, d) { return s + Number(d.value || 0); }, 0);
    var total = Number(totalValue) > 0 ? Number(totalValue) : sum;
    var hasMore = total > sum + 1;
    var data = list.map(function (d, i) {
      return { name: d.name, value: Number(d.value) || 0, itemStyle: { color: PALETTE[i % PALETTE.length] } };
    });
    var many = data.length > 8;
    var legend = many
      ? legendBase({
        type: 'scroll', orient: 'vertical', right: 0, top: 'middle', bottom: 'auto', left: 'auto', height: '88%',
        icon: 'circle', itemGap: 7, itemWidth: 8, itemHeight: 8,
        textStyle: { color: TEXT, fontSize: 9.5, fontFamily: FONT }
      })
      : legendBase({
        type: 'scroll', bottom: 0, top: 'auto', left: 'center', right: 'auto',
        icon: 'circle', itemGap: 10, itemWidth: 8, itemHeight: 8,
        textStyle: { color: TEXT, fontSize: 10, fontFamily: FONT }
      });
    var centerX = many ? '33%' : '50%';
    var titleTop = many ? '43%' : '30%';

    inst.setOption({
      animationDuration: 650,
      tooltip: tooltipBase({ trigger: 'item', formatter: '{b}<br/><b>{c}</b> 单 · {d}%' }),
      legend: legend,
      title: {
        text: fmtNum(total),
        subtext: hasMore ? '报修总量 · 含其他类型' : '报修总量',
        left: centerX,
        top: titleTop,
        textAlign: 'center',
        textStyle: { fontSize: many ? 18 : 21, fontWeight: 700, color: '#0f2544', fontFamily: FONT },
        subtextStyle: { fontSize: 9.5, color: MUTED, fontFamily: FONT }
      },
      series: [{
        name: '报修类型', type: 'pie',
        radius: many ? ['36%', '54%'] : ['42%', '61%'],
        center: [centerX, many ? '50%' : '40%'],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: '#fff', borderWidth: 2, borderRadius: 4 },
        label: { show: false },
        labelLine: { show: false },
        emphasis: { scale: true, scaleSize: 4, itemStyle: { shadowBlur: 12, shadowColor: 'rgba(15,37,68,.18)' } },
        data: data
      }]
    }, true);
  }

  function buildings(el, list) {
    list = Array.isArray(list) ? list : [];
    if (!list.length) return empty(el, '暂无楼栋数据', '接口未返回楼栋维度统计');
    var inst = ensure(el);
    if (!inst) return;
    var sorted = list.slice()
      .sort(function (a, b) { return (Number(b.value) || 0) - (Number(a.value) || 0); })
      .slice(0, 8);
    var names = sorted.map(function (d) { return d.name || '—'; });
    var orders = sorted.map(function (d) { return Number(d.value) || 0; });
    var rates = sorted.map(function (d) { return Number(d.timeout_rate) || 0; });
    var timeoutCounts = sorted.map(function (d, i) {
      return Math.max(0, Math.round(orders[i] * rates[i] / 100));
    });
    var normalCounts = orders.map(function (v, i) { return Math.max(0, v - timeoutCounts[i]); });

    inst.setOption({
      animationDuration: 650,
      tooltip: tooltipBase({
        trigger: 'axis',
        axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(37,99,235,.05)' } },
        formatter: function (params) {
          if (!params || !params.length) return '';
          var i = params[0].dataIndex;
          var head = '<div style="font-weight:600;margin-bottom:3px">' + esc(params[0].name) + '</div>';
          return head + '工单量：<b>' + orders[i] + '</b> 单<br/>超时工单：<b>' +
            timeoutCounts[i] + '</b> 单 · 超时率 <b>' + rates[i] + '%</b>';
        }
      }),
      legend: legendBase({ data: ['正常流转', '超时工单'] }),
      grid: { left: 6, right: 50, top: 34, bottom: 0, containLabel: true },
      yAxis: categoryAxis(names, {
        inverse: true,
        axisLabel: { color: TEXT, fontSize: 10.5, fontFamily: FONT },
        axisLine: { show: false }
      }),
      xAxis: valueAxis({ minInterval: 1 }),
      series: [
        {
          name: '正常流转', type: 'bar', stack: 'total', barWidth: 11,
          itemStyle: { borderRadius: [4, 0, 0, 4], color: linearGradient('#60a5fa', '#2563eb', true) },
          data: normalCounts
        },
        {
          name: '超时工单', type: 'bar', stack: 'total', barWidth: 11,
          itemStyle: { borderRadius: [0, 4, 4, 0], color: linearGradient('#fbbf24', '#f59e0b', true) },
          label: {
            show: true, position: 'right', color: MUTED, fontSize: 9.5, fontFamily: FONT,
            formatter: function (p) { return rates[p.dataIndex] + '%'; }
          },
          data: timeoutCounts
        }
      ]
    }, true);
  }

  function urgency(el, list) {
    list = Array.isArray(list) ? list : [];
    if (!list.length) return empty(el, '暂无紧急度数据', '接口未返回紧急程度统计');
    var inst = ensure(el);
    if (!inst) return;
    var order = { '紧急': 0, '高': 1, '中': 2, '低': 3 };
    var sorted = list.slice().sort(function (a, b) {
      return (order[a.name] == null ? 9 : order[a.name]) - (order[b.name] == null ? 9 : order[b.name]);
    });
    var colors = { '紧急': ['#f87171', '#ef4444'], '高': ['#fbbf24', '#f59e0b'], '中': ['#facc15', '#eab308'], '低': ['#4ade80', '#16a34a'] };
    var total = sorted.reduce(function (s, d) { return s + (Number(d.value) || 0); }, 0);
    var data = sorted.map(function (d, i) {
      var pair = colors[d.name] || ['#93c5fd', '#2563eb'];
      return {
        value: Number(d.value) || 0,
        itemStyle: { color: linearGradient(pair[0], pair[1], true), borderRadius: [0, 5, 5, 0] },
        label: { formatter: (total ? Math.round((Number(d.value) || 0) / total * 100) : 0) + '%' }
      };
    });

    inst.setOption({
      animationDuration: 650,
      tooltip: tooltipBase({
        trigger: 'item',
        formatter: function (p) {
          return p.name + '<br/><b>' + fmtNum(p.value) + '</b> 单 · 占比 ' + p.percent + '%';
        }
      }),
      grid: { left: 6, right: 38, top: 8, bottom: 0, containLabel: true },
      xAxis: valueAxis({ show: false }),
      yAxis: categoryAxis(sorted.map(function (d) { return d.name; }), {
        inverse: true,
        axisLine: { show: false },
        axisLabel: { color: TEXT, fontSize: 11, fontFamily: FONT, margin: 10 },
        axisTick: { show: false }
      }),
      series: [{
        type: 'bar', barWidth: 12, showBackground: true,
        backgroundStyle: { color: '#f2f7fd', borderRadius: 6 },
        itemStyle: { borderRadius: [0, 6, 6, 0] },
        label: { show: true, position: 'right', color: MUTED, fontSize: 10, fontFamily: FONT },
        data: data
      }]
    }, true);
  }

  function agentBars(el, agent) {
    agent = agent || {};
    var metrics = [
      { name: '理解准确率', value: Number(agent.understanding_accuracy) || 0, color: ['#60a5fa', '#2563eb'] },
      { name: '派单准确率', value: Number(agent.dispatch_accuracy) || 0, color: ['#67e8f9', '#06b6d4'] },
      { name: '验收通过率', value: Number(agent.verification_pass_rate) || 0, color: ['#86efac', '#16a34a'] }
    ];
    if (!metrics.some(function (m) { return m.value > 0; })) {
      return empty(el, '暂无 Agent 指标', '接口未返回 Agent 运行指标');
    }
    var inst = ensure(el);
    if (!inst) return;
    inst.setOption({
      animationDuration: 700,
      tooltip: tooltipBase({
        trigger: 'axis',
        axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(37,99,235,.05)' } },
        formatter: function (params) {
          if (!params || !params.length) return '';
          return params[0].name + '：<b>' + params[0].value + '%</b>';
        }
      }),
      grid: { left: 4, right: 44, top: 8, bottom: 0, containLabel: true },
      xAxis: valueAxis({ max: 100, show: false, splitLine: { show: false } }),
      yAxis: categoryAxis(metrics.map(function (m) { return m.name; }), {
        inverse: true,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: TEXT, fontSize: 11, fontFamily: FONT, margin: 10 }
      }),
      series: [{
        type: 'bar', barWidth: 13, showBackground: true,
        backgroundStyle: { color: '#f1f6fd', borderRadius: 7 },
        itemStyle: { borderRadius: 7 },
        label: {
          show: true, position: 'right', color: '#0f2544', fontSize: 11, fontWeight: 600,
          fontFamily: FONT, formatter: '{c}%'
        },
        data: metrics.map(function (m) {
          return { value: m.value, itemStyle: { color: linearGradient(m.color[0], m.color[1], true) } };
        })
      }]
    }, true);
  }

  function candidateScores(el, list) {
    list = (Array.isArray(list) ? list : []).filter(function (d) { return d && d.name; });
    if (!list.length) return empty(el, '暂无候选评分', '接口未返回候选维修工得分');
    var inst = ensure(el);
    if (!inst) return;
    var sorted = list.slice().sort(function (a, b) { return (Number(b.score) || 0) - (Number(a.score) || 0); }).slice(0, 4);
    inst.setOption({
      animationDuration: 600,
      tooltip: tooltipBase({
        trigger: 'axis',
        axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(37,99,235,.05)' } },
        formatter: function (params) {
          if (!params || !params.length) return '';
          return params[0].name + ' 综合评分：<b>' + Number(params[0].value).toFixed(1) + '</b>';
        }
      }),
      grid: { left: 2, right: 42, top: 6, bottom: 0, containLabel: true },
      xAxis: valueAxis({ max: 100, show: false, splitLine: { show: false } }),
      yAxis: categoryAxis(sorted.map(function (d) { return d.name; }), {
        inverse: true,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: TEXT, fontSize: 10.5, fontFamily: FONT, margin: 8 }
      }),
      series: [{
        type: 'bar', barWidth: 11, showBackground: true,
        backgroundStyle: { color: '#f1f6fd', borderRadius: 6 },
        itemStyle: { borderRadius: 6, color: linearGradient('#22d3ee', '#2563eb', true) },
        label: {
          show: true, position: 'right', color: '#0f2544', fontSize: 10.5, fontWeight: 600, fontFamily: FONT,
          formatter: function (p) { return Number(p.value).toFixed(1); }
        },
        data: sorted.map(function (d) { return Number(d.score) || 0; })
      }]
    }, true);
  }

  /* --------------------------------- 分析 --------------------------------- */

  function monthly(el, list) {
    list = Array.isArray(list) ? list : [];
    if (!list.length) return empty(el, '暂无月度数据', '接口未返回月度趋势');
    var inst = ensure(el);
    if (!inst) return;
    var labels = list.map(function (d) { return String(d.month || d.name || ''); });
    var orders = list.map(function (d) { return Number(d.orders != null ? d.orders : d.value) || 0; });
    var hasRate = list.some(function (d) { return d.closed_rate != null; });
    var rate = list.map(function (d) { return Number(d.closed_rate) || 0; });
    var rateMin = 80, rateMax = 100;
    if (hasRate) {
      rateMin = Math.max(0, Math.floor((Math.min.apply(null, rate) - 4) / 10) * 10);
      rateMax = Math.min(100, Math.ceil((Math.max.apply(null, rate) + 4) / 10) * 10);
      if (rateMax - rateMin < 10) rateMin = Math.max(0, rateMax - 10);
    }

    var series = [{
      name: '工单量', type: 'bar', barMaxWidth: 24, barWidth: '46%',
      itemStyle: { borderRadius: [4, 4, 0, 0], color: linearGradient('#3b82f6', '#2563eb') },
      data: orders
    }];
    if (hasRate) {
      series.push({
        name: '闭环率', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'circle', symbolSize: 6,
        showSymbol: true, z: 5, lineStyle: { width: 2.5, color: '#0d9488' },
        itemStyle: { color: '#0d9488', borderWidth: 2, borderColor: '#fff' },
        label: { show: false },
        data: rate
      });
    }

    inst.setOption({
      animationDuration: 650,
      tooltip: tooltipBase({ trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(37,99,235,.05)' } } }),
      legend: legendBase({ data: hasRate ? ['工单量', '闭环率'] : ['工单量'] }),
      grid: { left: 6, right: hasRate ? 12 : 8, top: 32, bottom: 0, containLabel: true },
      xAxis: categoryAxis(labels, {
        axisLine: { lineStyle: { color: AXIS_LINE } },
        axisLabel: {
          color: MUTED, fontSize: 10, fontFamily: FONT,
          formatter: function (value) {
            var text = String(value);
            return text.length >= 7 ? Number(text.slice(5, 7)) + '月' : text;
          }
        }
      }),
      yAxis: [
        valueAxis({ minInterval: 1 }),
        valueAxis({ min: rateMin, max: rateMax, splitLine: { show: false }, axisLabel: { color: MUTED, fontSize: 10, formatter: '{value}%' } })
      ],
      series: series
    }, true);
  }

  function timeoutCompare(el, list) {
    list = (Array.isArray(list) ? list : []).filter(function (d) { return d && (d.timeout_rate != null || d.value != null); });
    if (!list.length) return empty(el, '暂无超时数据', '接口未返回超时率统计');
    var inst = ensure(el);
    if (!inst) return;
    var sorted = list.slice()
      .map(function (d) { return { name: d.name || d.repair_type || '—', rate: Number(d.timeout_rate != null ? d.timeout_rate : d.value) || 0 }; })
      .sort(function (a, b) { return b.rate - a.rate; })
      .slice(0, 8);
    inst.setOption({
      animationDuration: 650,
      tooltip: tooltipBase({
        trigger: 'axis',
        axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(245,158,11,.07)' } },
        formatter: function (params) {
          if (!params || !params.length) return '';
          return params[0].name + ' 超时率：<b>' + params[0].value + '%</b>';
        }
      }),
      grid: { left: 6, right: 10, top: 12, bottom: 0, containLabel: true },
      xAxis: categoryAxis(sorted.map(function (d) { return d.name; }), {
        axisLabel: { color: MUTED, fontSize: 9.5, interval: 0, rotate: sorted.length > 5 ? 20 : 0, fontFamily: FONT },
        axisLine: { lineStyle: { color: AXIS_LINE } }
      }),
      yAxis: valueAxis({ axisLabel: { color: MUTED, fontSize: 10, formatter: '{value}%' } }),
      series: [{
        type: 'bar', barMaxWidth: 22, barWidth: '44%',
        itemStyle: { borderRadius: [4, 4, 0, 0], color: linearGradient('#fbbf24', '#ef4444') },
        label: { show: true, position: 'top', color: MUTED, fontSize: 9.5, formatter: '{c}%' },
        data: sorted.map(function (d) { return d.rate; })
      }]
    }, true);
  }

  function buildingRank(el, list) {
    list = Array.isArray(list) ? list : [];
    if (!list.length) return empty(el, '暂无楼栋数据', '接口未返回楼栋统计');
    var inst = ensure(el);
    if (!inst) return;
    var sorted = list.slice()
      .sort(function (a, b) { return (Number(b.value) || 0) - (Number(a.value) || 0); })
      .slice(0, 10);
    inst.setOption({
      animationDuration: 650,
      tooltip: tooltipBase({
        trigger: 'axis',
        axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(37,99,235,.05)' } },
        formatter: function (params) {
          if (!params || !params.length) return '';
          var idx = params[0].dataIndex;
          var item = sorted[idx] || {};
          var extra = item.timeout_rate != null ? '<br/>超时率：<b>' + item.timeout_rate + '%</b>' : '';
          return params[0].name + '：<b>' + params[0].value + '</b> 单' + extra;
        }
      }),
      grid: { left: 4, right: 42, top: 6, bottom: 0, containLabel: true },
      xAxis: valueAxis({ show: false, splitLine: { show: false } }),
      yAxis: categoryAxis(sorted.map(function (d) { return d.name || '—'; }), {
        inverse: true,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: TEXT, fontSize: 10.5, fontFamily: FONT, margin: 8 }
      }),
      series: [{
        type: 'bar', barWidth: 10, showBackground: true,
        backgroundStyle: { color: '#f1f6fd', borderRadius: 5 },
        itemStyle: { borderRadius: 5, color: linearGradient('#93c5fd', '#2563eb', true) },
        label: { show: true, position: 'right', color: MUTED, fontSize: 10, fontFamily: FONT },
        data: sorted.map(function (d) { return Number(d.value) || 0; })
      }]
    }, true);
  }

  function workerScatter(el, list) {
    list = (Array.isArray(list) ? list : []).filter(function (d) { return d && d.name; });
    if (!list.length) return empty(el, '暂无维修工数据', '接口未返回维修工统计');
    var inst = ensure(el);
    if (!inst) return;
    var points = list.map(function (d, i) {
      var satisfaction = Number(d.satisfaction) || 4.5;
      return {
        name: d.name,
        value: [Number(d.orders) || 0, Number(d.avg_duration) || 0, satisfaction],
        trade: d.trade || '',
        itemStyle: { color: PALETTE[i % PALETTE.length], opacity: 0.82 },
        symbolSize: Math.max(11, Math.min(30, 9 + (satisfaction - 4.0) * 26))
      };
    });
    inst.setOption({
      animationDuration: 700,
      tooltip: tooltipBase({
        trigger: 'item',
        formatter: function (p) {
          var d = p.data;
          return '<div style="font-weight:600;margin-bottom:3px">' + esc(d.name) + ' · ' + esc(d.trade) + '</div>' +
            '工单量：<b>' + d.value[0] + '</b> 单<br/>平均时长：<b>' + d.value[1] + '</b> 分钟<br/>满意度：<b>' + d.value[2] + '</b>';
        }
      }),
      grid: { left: 6, right: 30, top: 26, bottom: 0, containLabel: true },
      xAxis: valueAxis({
        name: '工单量', nameTextStyle: { color: MUTED, fontSize: 9.5, padding: [0, 0, 0, 0] },
        nameGap: 8, splitLine: { lineStyle: { color: GRID_LINE, type: 'dashed' } }
      }),
      yAxis: valueAxis({
        name: '平均时长 / 分钟', nameTextStyle: { color: MUTED, fontSize: 9.5 },
        nameGap: 10, splitLine: { lineStyle: { color: GRID_LINE, type: 'dashed' } }
      }),
      series: [{
        type: 'scatter',
        emphasis: { scale: true, itemStyle: { shadowBlur: 12, shadowColor: 'rgba(37,99,235,.3)' } },
        label: {
          show: true, position: 'top', distance: 4, color: TEXT, fontSize: 9.5, fontFamily: FONT,
          formatter: function (p) { return p.data.name; }
        },
        data: points
      }]
    }, true);
  }

  function hourly(el, list) {
    list = Array.isArray(list) ? list : [];
    if (!list.length) return empty(el, '暂无时段数据', '接口未返回小时分布');
    var inst = ensure(el);
    if (!inst) return;
    var byHour = {};
    list.forEach(function (d) { byHour[Number(d.hour)] = Number(d.orders) || 0; });
    var labels = [];
    var values = [];
    for (var i = 0; i < 24; i++) {
      labels.push(i + '时');
      values.push(byHour[i] || 0);
    }
    inst.setOption({
      animationDuration: 650,
      tooltip: tooltipBase({
        trigger: 'axis',
        axisPointer: { type: 'line', lineStyle: { color: '#bfdbfe', type: 'dashed' } },
        formatter: function (params) {
          if (!params || !params.length) return '';
          return params[0].name + ' 工单量：<b>' + params[0].value + '</b>';
        }
      }),
      grid: { left: 6, right: 12, top: 12, bottom: 0, containLabel: true },
      xAxis: categoryAxis(labels, {
        boundaryGap: false,
        axisLabel: { color: MUTED, fontSize: 9.5, interval: 3, fontFamily: FONT },
        axisLine: { lineStyle: { color: AXIS_LINE } }
      }),
      yAxis: valueAxis({ minInterval: 1 }),
      series: [{
        type: 'line', smooth: true, symbol: 'circle', symbolSize: 5, showSymbol: false,
        lineStyle: { width: 2.2, color: '#2563eb' },
        itemStyle: { color: '#2563eb', borderWidth: 2, borderColor: '#fff' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(37,99,235,.22)' },
            { offset: 1, color: 'rgba(37,99,235,0)' }
          ])
        },
        data: values
      }]
    }, true);
  }

  window.Charts = {
    palette: PALETTE,
    init: ensure,
    empty: empty,
    resizeAll: resizeAll,
    resizeIn: resizeIn,
    trend: trend,
    types: typesDonut,
    buildings: buildings,
    urgency: urgency,
    agentBars: agentBars,
    candidateScores: candidateScores,
    monthly: monthly,
    timeoutCompare: timeoutCompare,
    buildingRank: buildingRank,
    workerScatter: workerScatter,
    hourly: hourly
  };
})();
