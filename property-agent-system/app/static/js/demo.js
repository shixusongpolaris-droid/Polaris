/* ==========================================================================
   demo.js —— 智能派单页：流水线动画 + 完整闭环演示播放
   - 提交报修：POST /api/tickets，按返回的 logs/events 点亮 5 个 Agent 节点
   - 完整演示：POST /api/demo/run，按约 700ms 间隔逐条播放 steps
   - 接口不可用时降级为本地「示例回放」，并明确标注
   ========================================================================== */
(function () {
  'use strict';

  var AGENTS = {
    understanding: { name: '报修理解 Agent', short: '报修理解' },
    dispatch: { name: '派单决策 Agent', short: '派单决策' },
    monitoring: { name: '过程监控 Agent', short: '过程监控' },
    verification: { name: '闭环验证 Agent', short: '闭环验证' },
    coordinator: { name: '工单编排中心', short: '工单闭环' }
  };

  var PIPE_STAGE = {
    understanding: 0,
    dispatch: 1,
    monitoring: 2,
    verification: 3,
    coordinator: 4
  };

  var PIPE_DEFAULT = [
    '等待报修文本输入',
    '基于技能匹配、负载与区域生成派单方案',
    'SLA 倒计时扫描，超时预警与升级',
    '核验完工说明与照片，判定是否通过',
    '归档工单、沉淀决策日志与运行指标'
  ];

  var SAMPLE_TEXT = {
    elevator: {
      desc: '9栋电梯有业主被困在 5 至 6 层之间，请立即安排救援！',
      reporter: '李女士', phone: '13900001234', building: '9栋', unit: ''
    },
    pipe: {
      desc: '5栋1单元水管爆裂，楼道大量积水，业主家里停水，情况紧急。',
      reporter: '王女士', phone: '13800000000', building: '5栋', unit: '1单元'
    },
    drain: {
      desc: '3栋2单元厨房下水道堵塞，洗菜池返水，已自行疏通但效果不好。',
      reporter: '张先生', phone: '13700005678', building: '3栋', unit: '2单元'
    }
  };

  var state = {
    busy: false,
    demoBusy: false,
    demoTicket: null,
    timer: null,
    stepIndex: 0
  };

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function esc(str) {
    return String(str == null ? '' : str).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function agentName(code) { return (AGENTS[code] && AGENTS[code].name) || code || 'Agent'; }
  function agentTag(code) {
    var cls = AGENTS[code] ? code : 'coordinator';
    return '<span class="agent-tag ' + cls + '">' + esc(agentName(code)) + '</span>';
  }
  function truncate(str, len) {
    str = String(str || '');
    return str.length > len ? str.slice(0, len) + '…' : str;
  }

  /* ------------------------------ 流水线控制 ------------------------------ */
  function resetPipeline() {
    PIPE_DEFAULT.forEach(function (text, i) {
      var node = $('#pipe' + (i + 1));
      if (!node) return;
      node.classList.remove('active', 'done', 'warn');
      var summary = $('.pipe-summary', node);
      if (summary) summary.textContent = text;
      var dur = $('.pipe-dur', node);
      if (dur) { dur.hidden = true; dur.textContent = ''; }
      var label = $('.pipe-state', node);
      if (label) label.textContent = '待触发';
    });
  }

  function setPipe(index, status, opts) {
    var node = $('#pipe' + (index + 1));
    if (!node) return;
    opts = opts || {};
    node.classList.remove('active', 'done', 'warn');
    if (status === 'active') node.classList.add('active');
    if (status === 'done') node.classList.add('done');
    if (status === 'warn') node.classList.add('warn');
    var summary = $('.pipe-summary', node);
    if (summary && opts.summary) summary.textContent = opts.summary;
    var dur = $('.pipe-dur', node);
    if (dur) {
      if (opts.duration) { dur.hidden = false; dur.textContent = opts.duration; }
      else { dur.hidden = true; dur.textContent = ''; }
    }
    var stateEl = $('.pipe-state', node);
    if (stateEl) {
      stateEl.textContent = status === 'active' ? '执行中'
        : status === 'done' ? '已完成'
          : status === 'warn' ? '需关注'
            : '待触发';
    }
  }

  function markPipeByAgent(agent, status, opts) {
    var idx = PIPE_STAGE[agent];
    if (idx == null) return;
    setPipe(idx, status, opts);
  }

  function finishPendingPipes(text) {
    for (var i = 0; i < 5; i++) {
      var node = $('#pipe' + (i + 1));
      if (!node || node.classList.contains('done') || node.classList.contains('warn')) continue;
      node.classList.remove('active');
      var stateEl = $('.pipe-state', node);
      if (stateEl) stateEl.textContent = '等待触发';
      var summary = $('.pipe-summary', node);
      if (summary && text) summary.textContent = text;
    }
  }

  function durationText(ms) {
    var n = Number(ms);
    if (!n || isNaN(n)) return '';
    return n >= 1000 ? (n / 1000).toFixed(2) + 's' : n.toFixed(1) + 'ms';
  }

  /* ------------------------------ 执行流 UI ------------------------------ */
  function streamHead(show) {
    var head = $('#demoStreamHead');
    if (head) head.hidden = !show;
  }

  function clearStream() {
    var stream = $('#demoStream');
    if (stream) stream.innerHTML = '';
    streamHead(false);
    var badge = $('#demoBadge');
    if (badge) badge.hidden = true;
    var progress = $('#demoProgressText');
    if (progress) progress.textContent = '';
  }

  function appendStep(step, sample) {
    var stream = $('#demoStream');
    if (!stream) return;
    var statusCls = UI.statusClass(step.status);
    var label = step.status_label || UI.statusLabel(step.status) || step.status || '';
    var item = document.createElement('div');
    item.className = 'demo-step';
    item.innerHTML =
      '<div class="demo-step-time">' + esc(step.time || '') + '</div>' +
      '<div class="demo-step-main">' +
      '<div class="demo-step-top">' + agentTag(step.agent) +
      '<strong>' + esc(step.title || '决策事件') + '</strong>' +
      '<span class="status-badge ' + statusCls + '">' + esc(label) + '</span>' +
      '</div>' +
      '<div class="demo-step-summary">' + esc(step.summary || '') + '</div>' +
      '</div>';
    stream.appendChild(item);
    stream.scrollTop = stream.scrollHeight;
    var badge = $('#demoBadge');
    if (badge) badge.hidden = !sample;
  }

  function updateProgress(done, total) {
    var el = $('#demoProgressText');
    if (el) el.textContent = done + ' / ' + total + (done >= total ? ' · 已完成' : '');
  }

  /* ------------------------------ 结果卡片 ------------------------------ */
  function extractCandidates(logs) {
    var arr = null;
    (logs || []).forEach(function (log) {
      if (arr || !log || !log.output || typeof log.output !== 'object') return;
      var out = log.output;
      ['candidates', 'ranking', 'scores', 'worker_scores', 'candidate_scores'].some(function (key) {
        if (Array.isArray(out[key])) { arr = out[key]; return true; }
        if (out.data && Array.isArray(out.data[key])) { arr = out.data[key]; return true; }
        return false;
      });
    });
    if (!arr) return [];
    var list = arr.map(function (item) {
      if (typeof item === 'string') return { name: item, score: 0 };
      var name = item.name || item.worker_name || (item.worker && item.worker.name) ||
        item.worker || item.worker_id || item.id;
      var score = item.score != null ? item.score
        : item.total_score != null ? item.total_score
          : item.total != null ? item.total : null;
      if (score == null && item.scores && typeof item.scores === 'object') {
        var sum = 0;
        Object.keys(item.scores).forEach(function (k) { if (typeof item.scores[k] === 'number') sum += item.scores[k]; });
        score = sum;
      }
      var raw = Number(score) || 0;
      // 后端候选评分为 0~1 归一化分值，统一放大为百分制展示
      var normalized = raw > 0 && raw <= 1 ? Math.round(raw * 1000) / 10 : raw;
      return { name: String(name || '—'), score: normalized };
    }).filter(function (x) { return x.score > 0; });
    return list;
  }

  function componentBasis(dispatchLog) {
    var out = dispatchLog && dispatchLog.output;
    if (!out || typeof out !== 'object') return null;
    var candidates = Array.isArray(out.candidates) ? out.candidates : [];
    var cand = candidates[0] || out;
    var comp = (cand && (cand.components || cand.breakdown)) || out.components;
    if (!comp || typeof comp !== 'object') return null;
    var labels = {
      skill: '技能匹配', zone: '片区匹配', load: '当前负载',
      rating: '历史评分', reliability: '历史准时率', speed: '响应速度', sla: 'SLA 保障'
    };
    var list = [];
    Object.keys(labels).forEach(function (key) {
      if (comp[key] == null) return;
      var value = Number(comp[key]);
      if (!isFinite(value)) return;
      var text = labels[key] + ' ' + (value <= 1 ? value.toFixed(2) : value.toFixed(1));
      if (key === 'load' && cand.active_tickets != null) {
        text += '（当前在途 ' + cand.active_tickets + ' 单' + (cand.max_daily ? ' / 上限 ' + cand.max_daily : '') + '）';
      }
      if (key === 'rating' && cand.rating != null) text += '（' + cand.rating + ' 分）';
      list.push(text);
    });
    if (out.sla_minutes != null) list.push('SLA 时限 ' + out.sla_minutes + ' 分钟内完成');
    return list.length ? list.slice(0, 6) : null;
  }

  function parseBasis(log, ticket) {
    var list = [];
    if (log && log.rationale) {
      list = String(log.rationale).split(/[；;\n]+/).map(function (s) { return s.trim(); }).filter(Boolean);
      if (list.length === 1 && list[0].length > 46) {
        var parts = list[0].split(/[，,]/).map(function (s) { return s.trim(); }).filter(function (s) { return s.length > 3; });
        if (parts.length > 1) list = parts;
      }
    }
    if (!list.length) {
      list = [
        '技能匹配：' + (ticket.trade || '综合维修') + ' 工种与报修类型匹配',
        '区域就近：' + (ticket.building || ticket.location || '—') + ' 责任区',
        'SLA 时限：' + (ticket.sla_minutes || '—') + ' 分钟内完成',
        '负载均衡：结合当前在途工单与历史评分综合排序'
      ];
    }
    return list.slice(0, 6);
  }

  function renderResult(ticket, logs, opts) {
    opts = opts || {};
    var wrap = $('#dispatchResult');
    if (!wrap) return;
    var dispatchLog = null;
    (logs || []).forEach(function (log) { if (!dispatchLog && log && log.agent === 'dispatch') dispatchLog = log; });
    var candidates = extractCandidates(logs);
    var selectedScore = candidates.length ? candidates[0].score : null;
    var basis = componentBasis(dispatchLog) || parseBasis(dispatchLog, ticket);
    var name = ticket.worker_name || '待派单';
    var initial = name !== '待派单' ? name.slice(0, 1) : '?';
    var urgencyCls = UI.urgencyClass(ticket.urgency);

    var html = '<div class="dispatch-result-inner">' +
      '<div class="result-left">' +
      '<div class="result-worker">' +
      '<div class="worker-avatar' + (ticket.worker_name ? '' : ' blank') + '">' + esc(initial) + '</div>' +
      '<div class="result-worker-info"><strong>' + esc(name) + '</strong>' +
      '<span>' + esc(ticket.trade || '—') + (ticket.worker_id ? ' · ' + esc(ticket.worker_id) : '') + '</span></div>' +
      (selectedScore != null
        ? '<span class="badge-soft green">综合评分 ' + Number(selectedScore).toFixed(1) + '</span>'
        : '<span class="badge-soft">Agent 匹配</span>') +
      '</div>' +
      '<div class="result-meta">' +
      '<div class="result-meta-item"><span>SLA 处理时限</span><strong class="accent">' + esc(ticket.sla_minutes || '—') + ' 分钟</strong></div>' +
      '<div class="result-meta-item"><span>紧急程度</span><strong><span class="urgency ' + urgencyCls + '">' + esc(ticket.urgency || '中') + '</span></strong></div>' +
      '<div class="result-meta-item"><span>要求完成时间</span><strong>' + esc(UI.shortTime(ticket.deadline) || '—') + '</strong></div>' +
      '<div class="result-meta-item"><span>派单方式</span><strong>' + (ticket.auto_dispatched ? '自动派单' : '人工派单') + '</strong></div>' +
      '</div>' +
      '<div class="result-section-title">评分依据<span class="badge-soft blue">可解释</span></div>' +
      '<ul class="basis-list">' + basis.map(function (b) {
        return '<li><span class="basis-check"><svg class="icon"><use href="#i-check"></use></svg></span><span>' + esc(b) + '</span></li>';
      }).join('') + '</ul>' +
      '<div class="result-actions">' +
      '<button class="btn primary sm" type="button" data-open-ticket="' + esc(ticket.id) + '">' +
      '<svg class="icon"><use href="#i-arrow-right"></use></svg>查看工单详情</button>' +
      '<button class="btn ghost sm" type="button" data-demo-reset="1">重新演示</button>' +
      '</div>' +
      '</div>' +
      '<div class="result-right">' +
      '<div class="result-section-title">候选维修工得分<span class="badge-soft">TOP ' + Math.max(candidates.length, 0) + '</span></div>' +
      '<div id="chartCandidates"></div>' +
      (opts.caseInfo
        ? '<div class="result-case"><strong>演示案例：' + esc(opts.caseInfo.title || ticket.title || '—') + '</strong><br/>' +
        '维修工：' + esc(opts.caseInfo.worker || name) + ' · SLA ' + esc(opts.caseInfo.sla_minutes || ticket.sla_minutes || '—') +
        ' 分钟' + (opts.caseInfo.total_minutes != null ? ' · 实际 ' + esc(opts.caseInfo.total_minutes) + ' 分钟' : '') +
        (opts.caseInfo.storyline ? '<br/>剧情：' + esc(opts.caseInfo.storyline) : '') +
        (opts.caseInfo.summary ? '<br/>' + esc(opts.caseInfo.summary) : '') + '</div>'
        : '') +
      '</div>' +
      '</div>';

    wrap.innerHTML = html;
    var sampleBadge = $('#resultSampleBadge');
    if (sampleBadge) sampleBadge.hidden = !opts.sample;

    var chartEl = $('#chartCandidates');
    if (chartEl) {
      if (candidates.length) {
        Charts.candidateScores(chartEl, candidates);
      } else {
        Charts.empty(chartEl, '暂无候选评分', '接口未返回候选维修工得分数据');
      }
    }
    state.demoTicket = ticket;
  }

  /* ------------------------------ 提交报修 ------------------------------ */
  async function submitTicket() {
    if (state.busy || state.demoBusy) return;
    var descEl = $('#dispatchDesc');
    var desc = (descEl && descEl.value || '').trim();
    if (!desc) {
      UI.toast('请先填写报修描述', 'warn');
      if (descEl) descEl.focus();
      return;
    }
    var payload = {
      description: desc,
      reporter_name: ($('#dispatchReporter') || {}).value || '',
      reporter_phone: ($('#dispatchPhone') || {}).value || '',
      building: ($('#dispatchBuilding') || {}).value || '',
      unit: ($('#dispatchUnit') || {}).value || '',
      room: ''
    };

    var btn = $('#dispatchSubmit');
    setBusy(btn, true, '提交中…');
    state.busy = true;
    clearStream();
    resetPipeline();
    setPipe(0, 'active', { summary: 'Agent 正在解析报修文本…' });

    var sample = false;
    var detail;
    try {
      detail = await API.createTicket(payload);
      detail = normalizeDetail(detail, payload);
    } catch (err) {
      sample = true;
      detail = SampleData.simulateDispatch(payload);
      detail.is_sample = true;
      UI.toast('工单接口不可用，已生成本地示例派单结果', 'warn', 4200);
    }

    try {
      await animatePipeline(detail, { sample: sample });
      renderResult(detail.ticket, detail.logs, { sample: sample || !!detail.is_sample });
      if (!sample) {
        UI.toast('派单完成：' + (detail.ticket.worker_name || '—') + ' · SLA ' + (detail.ticket.sla_minutes || '—') + ' 分钟', 'success');
      }
      if (window.App && detail.ticket && detail.ticket.id) {
        window.App.rememberTicket(detail);
      }
    } finally {
      state.busy = false;
      setBusy(btn, false);
    }
  }

  function normalizeDetail(detail, payload) {
    if (!detail || typeof detail !== 'object') {
      return SampleData.simulateDispatch(payload);
    }
    if (!detail.ticket && detail.id) detail = { ticket: detail, events: [], logs: [], warnings: [] };
    detail.events = Array.isArray(detail.events) ? detail.events : [];
    detail.logs = Array.isArray(detail.logs) ? detail.logs : [];
    detail.warnings = Array.isArray(detail.warnings) ? detail.warnings : [];
    return detail;
  }

  function findLog(logs, agent) {
    for (var i = 0; i < (logs || []).length; i++) {
      if (logs[i] && logs[i].agent === agent) return logs[i];
    }
    return null;
  }

  async function animatePipeline(detail, opts) {
    opts = opts || {};
    var logs = detail.logs || [];
    var ticket = detail.ticket || {};
    var events = detail.events || [];
    var understanding = findLog(logs, 'understanding');
    var dispatch = findLog(logs, 'dispatch');
    var monitoring = findLog(logs, 'monitoring');
    var verification = findLog(logs, 'verification');
    var closed = String(ticket.status) === 'closed';

    if (understanding) {
      setPipe(0, 'active', { summary: '识别类型「' + (ticket.repair_type || '—') + '」，紧急度「' + (ticket.urgency || '—') + '」' });
      await wait(280);
      setPipe(0, 'done', {
        summary: '识别类型「' + (ticket.repair_type || '—') + '」，紧急度「' + (ticket.urgency || '—') + '」，置信度 ' + (understanding.confidence != null ? understanding.confidence : '—'),
        duration: durationText(understanding.duration_ms)
      });
    } else {
      setPipe(0, 'done', { summary: '识别类型「' + (ticket.repair_type || '—') + '」，紧急度「' + (ticket.urgency || '—') + '」' });
    }

    await wait(220);
    if (dispatch) {
      setPipe(1, 'active', { summary: '正在按技能 / 区域 / 负载 / 评分计算候选维修工…' });
      await wait(320);
      setPipe(1, 'done', {
        summary: '派单至 ' + (ticket.worker_name || '—') + '（' + (ticket.trade || '—') + '），SLA ' + (ticket.sla_minutes || '—') + ' 分钟',
        duration: durationText(dispatch.duration_ms)
      });
    } else {
      setPipe(1, 'warn', { summary: '等待派单结果' });
    }

    await wait(260);
    var warningCount = (detail.warnings || []).length;
    var monitorEvent = events.some(function (e) {
      var t = String((e && e.event_type) || '');
      return t.indexOf('warning') >= 0 || t.indexOf('accept') >= 0 || t.indexOf('monitor') >= 0 || t.indexOf('escalat') >= 0;
    });
    if (monitoring || warningCount > 0 || monitorEvent) {
      setPipe(2, 'done', {
        summary: warningCount > 0 ? '生成 ' + warningCount + ' 条预警，已升级处理' : 'SLA 扫描完成，当前无超时风险',
        duration: monitoring ? durationText(monitoring.duration_ms) : ''
      });
    } else {
      setPipe(2, 'active', { summary: '工单已进入 SLA 监控队列，等待接单与处理' });
    }

    await wait(240);
    if (verification) {
      var verdict = verification.output && (verification.output.verdict || verification.output.pass);
      var returned = String(ticket.status) === 'returned' || verdict === 'returned' || verdict === false;
      setPipe(3, returned ? 'warn' : 'done', {
        summary: returned ? '验证未通过，工单已退回整改' : '完工资料齐全，验证通过',
        duration: durationText(verification.duration_ms)
      });
    } else {
      nodeWaiting(3, '等待维修工提交完工后触发验证');
    }

    await wait(240);
    if (closed) {
      setPipe(4, 'done', { summary: '工单已归档闭环，处理时长 ' + (ticket.duration_minutes || '—') + ' 分钟' });
    } else {
      nodeWaiting(4, '工单闭环后自动归档');
    }
  }

  function nodeWaiting(index, summary) {
    var node = $('#pipe' + (index + 1));
    if (!node) return;
    node.classList.remove('active', 'done', 'warn');
    var summaryEl = $('.pipe-summary', node);
    if (summaryEl) summaryEl.textContent = summary;
    var stateEl = $('.pipe-state', node);
    if (stateEl) stateEl.textContent = '等待触发';
  }

  /* ------------------------------ 完整演示 ------------------------------ */
  async function runFullDemo() {
    if (state.busy || state.demoBusy) return;
    var btn = $('#demoRunBtn');
    setBusy(btn, true, '演示运行中…');
    state.demoBusy = true;
    clearStream();
    resetPipeline();
    streamHead(true);
    var progress = $('#demoProgressText');
    if (progress) progress.textContent = '0 / 0 · 准备中';

    var data;
    var sample = false;
    try {
      data = await API.demoRun();
      if (!data || !Array.isArray(data.steps)) throw new Error('演示数据格式异常');
    } catch (err) {
      sample = true;
      data = SampleData.demoRun();
      UI.toast('演示接口不可用，已切换为本地示例回放', 'warn', 4200);
    }

    try {
      await playSteps(data, sample);
      var logs = [];
      var hint = null;
      try {
        if (sample) {
          hint = SampleData.demoDetail();
          logs = (hint && hint.logs) || [];
        } else if (data.ticket && data.ticket.id) {
          var detail = await API.ticket(data.ticket.id);
          if (detail && Array.isArray(detail.logs)) logs = detail.logs;
          if (detail && detail.ticket) hint = detail;
        }
      } catch (e) { logs = []; }
      if (window.App && data.ticket && data.ticket.id) {
        window.App.rememberTicket(hint || { ticket: data.ticket, events: [], logs: logs, warnings: data.warnings || [] });
      }
      renderResult(data.ticket || {}, logs, { sample: sample, caseInfo: data.case });
      if (!sample) UI.toast('完整闭环演示完成', 'success');
    } finally {
      state.demoBusy = false;
      setBusy(btn, false);
    }
  }

  function pipeIndexForStep(step) {
    var type = String((step && step.event_type) || '').toLowerCase();
    if (type.indexOf('created') >= 0) return -1;
    if (type.indexOf('understand') >= 0 || type.indexOf('parse') >= 0) return 0;
    if (type.indexOf('dispatch') >= 0 || type.indexOf('assign') >= 0) return 1;
    if (type.indexOf('accept') >= 0 || type.indexOf('warning') >= 0 || type.indexOf('monitor') >= 0 ||
      type.indexOf('processing') >= 0 || type.indexOf('escalat') >= 0) return 2;
    if (type.indexOf('verif') >= 0 || type.indexOf('return') >= 0 || type.indexOf('complet') >= 0) return 3;
    if (type.indexOf('closed') >= 0 || type.indexOf('archive') >= 0) return 4;
    var fallback = PIPE_STAGE[step && step.agent];
    return fallback == null ? -1 : fallback;
  }

  function playSteps(data, sample) {
    return new Promise(function (resolve) {
      var steps = (data.steps || []).slice();
      var total = steps.length;
      var ticket = data.ticket || {};
      var idx = 0;
      state.stepIndex = 0;
      updateProgress(0, total);

      if (state.timer) clearTimeout(state.timer);
      function tick() {
        if (idx >= steps.length) {
          finishPendingPipes('全流程已完成 · 工单已闭环');
          updateProgress(total, total);
          resolve();
          return;
        }
        var step = steps[idx];
        var pipeIndex = pipeIndexForStep(step);
        if (pipeIndex != null) {
          setPipe(pipeIndex, 'active', { summary: truncate(step.summary, 48), duration: step.time });
          setTimeout(function () {
            setPipe(pipeIndex, 'done', { summary: truncate(step.summary, 48), duration: step.time });
          }, 380);
        }
        appendStep(step, sample);
        idx++;
        state.stepIndex = idx;
        updateProgress(idx, total);
        state.timer = setTimeout(tick, 700);
      }
      tick();
    });
  }

  /* ------------------------------ 其他交互 ------------------------------ */
  function setBusy(btn, busy, text) {
    if (!btn) return;
    if (busy) {
      btn.dataset.original = btn.innerHTML;
      btn.disabled = true;
      btn.classList.add('loading');
      btn.innerHTML = '<svg class="icon"><use href="#i-refresh"></use></svg>' + esc(text || '处理中…');
    } else {
      btn.disabled = false;
      btn.classList.remove('loading');
      if (btn.dataset.original) {
        btn.innerHTML = btn.dataset.original;
        delete btn.dataset.original;
      }
    }
  }

  function wait(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function fillSample(key) {
    var sample = SAMPLE_TEXT[key];
    if (!sample) return;
    var map = {
      '#dispatchDesc': sample.desc,
      '#dispatchReporter': sample.reporter,
      '#dispatchPhone': sample.phone,
      '#dispatchBuilding': sample.building,
      '#dispatchUnit': sample.unit
    };
    Object.keys(map).forEach(function (sel) {
      var el = $(sel);
      if (el) el.value = map[sel];
    });
    var desc = $('#dispatchDesc');
    if (desc) desc.focus();
  }

  function resetDemo() {
    resetPipeline();
    clearStream();
    var wrap = $('#dispatchResult');
    if (wrap) {
      wrap.innerHTML = '<div class="empty-state">' +
        '<div class="empty-icon"><svg class="icon"><use href="#i-spark"></use></svg></div>' +
        '<strong>等待派单</strong>' +
        '<p>提交报修或运行完整演示后，这里将展示维修工、SLA 评分依据与候选得分。</p>' +
        '</div>';
    }
    var sampleBadge = $('#resultSampleBadge');
    if (sampleBadge) sampleBadge.hidden = true;
    state.demoTicket = null;
  }

  function bindEvents() {
    var submit = $('#dispatchSubmit');
    if (submit && !submit.dataset.bound) {
      submit.dataset.bound = '1';
      submit.addEventListener('click', submitTicket);
    }
    var demoBtn = $('#demoRunBtn');
    if (demoBtn && !demoBtn.dataset.bound) {
      demoBtn.dataset.bound = '1';
      demoBtn.addEventListener('click', runFullDemo);
    }
    $$('.sample-chips .chip').forEach(function (chip) {
      if (chip.dataset.bound) return;
      chip.dataset.bound = '1';
      chip.addEventListener('click', function () { fillSample(chip.dataset.sample); });
    });
    var desc = $('#dispatchDesc');
    if (desc && !desc.dataset.bound) {
      desc.dataset.bound = '1';
      desc.addEventListener('keydown', function (e) {
        if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') submitTicket();
      });
    }
    var resultWrap = $('#dispatchResult');
    if (resultWrap && !resultWrap.dataset.bound) {
      resultWrap.dataset.bound = '1';
      resultWrap.addEventListener('click', function (e) {
        var openBtn = e.target.closest('[data-open-ticket]');
        if (openBtn) {
          var id = openBtn.getAttribute('data-open-ticket');
          if (window.App) window.App.openTicket(id, state.demoTicket);
          return;
        }
        var resetBtn = e.target.closest('[data-demo-reset]');
        if (resetBtn) resetDemo();
      });
    }
  }

  window.Demo = {
    init: function () {
      bindEvents();
    },
    enter: function () {
      bindEvents();
    },
    runFull: runFullDemo,
    submit: submitTicket,
    reset: resetDemo
  };
})();
