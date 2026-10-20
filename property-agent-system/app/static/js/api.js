/* ==========================================================================
   api.js —— 后端接口封装
   - 同源请求，默认 12s 超时，AbortController 控制
   - 统一错误对象：{ kind: 'timeout' | 'network' | 'http' | 'parse', status, message }
   - 演示接口 /api/demo/run 单独 60s 超时
   ========================================================================== */
(function () {
  'use strict';

  var BASE = '';

  function buildError(message, kind, extra) {
    var err = new Error(message || '请求失败');
    err.kind = kind || 'unknown';
    if (extra) {
      Object.keys(extra).forEach(function (k) { err[k] = extra[k]; });
    }
    return err;
  }

  async function request(path, options) {
    options = options || {};
    var method = options.method || 'GET';
    var timeout = options.timeout || 12000;
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, timeout);
    var init = {
      method: method,
      signal: controller.signal,
      headers: { 'Accept': 'application/json' },
      cache: 'no-store'
    };
    if (options.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(options.body);
    }

    var res;
    try {
      res = await fetch(BASE + path, init);
    } catch (err) {
      clearTimeout(timer);
      if (err && err.name === 'AbortError') {
        throw buildError('请求超时，后端服务可能未响应', 'timeout');
      }
      throw buildError('无法连接后端服务', 'network');
    }
    clearTimeout(timer);

    var text = '';
    try { text = await res.text(); } catch (e) { text = ''; }

    var data = null;
    if (text) {
      try { data = JSON.parse(text); } catch (e) { data = text; }
    }

    if (!res.ok) {
      var detail = data && typeof data === 'object' ? data.detail : null;
      throw buildError(detail || ('接口请求失败（HTTP ' + res.status + '）'), 'http', {
        status: res.status,
        data: data
      });
    }
    return data;
  }

  function qs(params) {
    var sp = new URLSearchParams();
    Object.keys(params || {}).forEach(function (key) {
      var val = params[key];
      if (val === undefined || val === null || val === '') return;
      sp.set(key, val);
    });
    var s = sp.toString();
    return s ? '?' + s : '';
  }

  function describeError(err) {
    if (!err) return '未知错误';
    if (err.kind === 'timeout') return '请求超时，后端服务未响应';
    if (err.kind === 'network') return '无法连接后端服务（请确认后端已启动）';
    if (err.kind === 'http') {
      if (err.status === 404) return '接口或数据不存在（404）';
      if (err.status === 422) return '请求参数校验失败（422）';
      if (err.status === 500) return '后端服务内部错误（500）';
      return err.message || ('请求失败（HTTP ' + err.status + '）');
    }
    return err.message || '请求失败';
  }

  /* ======================================================================
     社区扩展（v1.1）：小区守护 / 停车管理
     接口契约见 docs/API契约-社区扩展.md
     ====================================================================== */
  var community = {
    overview: function () {
      return request('/api/community/overview', { timeout: 15000 });
    },

    residents: function (params) {
      return request('/api/community/residents' + qs(params || {}), { timeout: 15000 });
    },

    resident: function (residentId) {
      return request('/api/community/residents/' + encodeURIComponent(residentId), { timeout: 12000 });
    },

    createResident: function (payload) {
      return request('/api/community/residents', { method: 'POST', body: payload || {}, timeout: 20000 });
    },

    updateResident: function (residentId, payload) {
      return request('/api/community/residents/' + encodeURIComponent(residentId), {
        method: 'PUT', body: payload || {}, timeout: 20000
      });
    },

    access: function (payload) {
      return request('/api/community/access', { method: 'POST', body: payload || {}, timeout: 20000 });
    },

    scan: function (payload) {
      return request('/api/community/scan', { method: 'POST', body: payload || {}, timeout: 20000 });
    },

    alerts: function (params) {
      return request('/api/community/alerts' + qs(params || {}), { timeout: 12000 });
    },

    ackAlert: function (alertId, payload) {
      return request('/api/community/alerts/' + encodeURIComponent(alertId) + '/ack', {
        method: 'POST', body: payload || {}, timeout: 20000
      });
    }
  };

  var parking = {
    dashboard: function () {
      return request('/api/parking/dashboard', { timeout: 15000 });
    },

    entry: function (payload) {
      return request('/api/parking/entry', { method: 'POST', body: payload || {}, timeout: 20000 });
    },

    exit: function (payload) {
      return request('/api/parking/exit', { method: 'POST', body: payload || {}, timeout: 25000 });
    },

    pay: function (sessionId, payload) {
      return request('/api/parking/sessions/' + encodeURIComponent(sessionId) + '/pay', {
        method: 'POST', body: payload || {}, timeout: 20000
      });
    },

    listSessions: function (params) {
      return request('/api/parking/sessions' + qs(params || {}), { timeout: 15000 });
    },

    vehicles: function (params) {
      return request('/api/parking/vehicles' + qs(params || {}), { timeout: 15000 });
    }
  };

  window.API = {
    request: request,

    describeError: describeError,

    /* 社区扩展：命名空间 + 扁平别名，方便按文档调用 */
    community: community,
    parking: parking,
    communityOverview: community.overview,
    communityResidents: community.residents,
    communityResident: community.resident,
    communityCreateResident: community.createResident,
    communityUpdateResident: community.updateResident,
    communityAccess: community.access,
    communityScan: community.scan,
    communityAlerts: community.alerts,
    communityAckAlert: community.ackAlert,
    parkingDashboard: parking.dashboard,
    parkingEntry: parking.entry,
    parkingExit: parking.exit,
    parkingPay: parking.pay,
    parkingSessions: parking.listSessions,
    parkingVehicles: parking.vehicles,

    health: function () {
      return request('/api/health', { timeout: 5000 });
    },

    summary: function (days) {
      return request('/api/dashboard/summary' + qs({ days: days || 30 }), { timeout: 15000 });
    },

    tickets: function (params) {
      return request('/api/tickets' + qs(params || {}), { timeout: 15000 });
    },

    ticket: function (id) {
      return request('/api/tickets/' + encodeURIComponent(id), { timeout: 12000 });
    },

    createTicket: function (payload) {
      return request('/api/tickets', { method: 'POST', body: payload, timeout: 30000 });
    },

    acceptTicket: function (id, payload) {
      return request('/api/tickets/' + encodeURIComponent(id) + '/accept', {
        method: 'POST', body: payload || {}, timeout: 20000
      });
    },

    completeTicket: function (id, payload) {
      return request('/api/tickets/' + encodeURIComponent(id) + '/complete', {
        method: 'POST', body: payload || {}, timeout: 30000
      });
    },

    reassignTicket: function (id, payload) {
      return request('/api/tickets/' + encodeURIComponent(id) + '/reassign', {
        method: 'POST', body: payload || {}, timeout: 20000
      });
    },

    workers: function () {
      return request('/api/workers', { timeout: 10000 });
    },

    warnings: function (status) {
      return request('/api/warnings' + qs({ status: status || 'open' }), { timeout: 10000 });
    },

    monitorScan: function (payload) {
      return request('/api/monitor/scan', { method: 'POST', body: payload || {}, timeout: 20000 });
    },

    logs: function (params) {
      return request('/api/logs' + qs(params || {}), { timeout: 15000 });
    },

    agentEval: function () {
      return request('/api/analytics/agent-evaluation', { timeout: 20000 });
    },

    typeStats: function () {
      return request('/api/analytics/type-stats', { timeout: 20000 });
    },

    demoRun: function () {
      return request('/api/demo/run', { method: 'POST', body: {}, timeout: 60000 });
    },

    demoLatest: function () {
      return request('/api/demo/latest', { timeout: 8000 });
    }
  };
})();
