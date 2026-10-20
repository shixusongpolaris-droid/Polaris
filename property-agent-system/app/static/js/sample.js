/* ==========================================================================
   sample.js —— 接口不可用时的演示占位数据
   说明：仅在 /api 请求失败（服务未启动 / 404 / 网络错误）时启用，
        界面会明确标注「示例数据」，正常有接口数据时绝不覆盖真实数据。
   ========================================================================== */
(function () {
  'use strict';

  var WORKERS = [
    { id: 'W001', name: '张伟', trade: '电梯维保', zone: 'A', level: '高级', rating: 4.8, avg_duration_min: 45, max_daily: 6, skills: ['电梯', '困人救援', '曳引机'] },
    { id: 'W002', name: '李强', trade: '电梯维保', zone: 'B', level: '中级', rating: 4.6, avg_duration_min: 52, max_daily: 6, skills: ['电梯', '门机', '控制系统'] },
    { id: 'W003', name: '王建国', trade: '水电工', zone: 'A', level: '高级', rating: 4.7, avg_duration_min: 72, max_daily: 8, skills: ['给排水', '水管', '下水道', '阀门'] },
    { id: 'W004', name: '赵磊', trade: '水电工', zone: 'B', level: '中级', rating: 4.5, avg_duration_min: 88, max_daily: 8, skills: ['给排水', '水管', '水泵'] },
    { id: 'W005', name: '陈明', trade: '电工', zone: 'A', level: '高级', rating: 4.6, avg_duration_min: 65, max_daily: 8, skills: ['电气', '照明', '配电箱'] },
    { id: 'W006', name: '刘洋', trade: '电工', zone: 'C', level: '中级', rating: 4.4, avg_duration_min: 82, max_daily: 8, skills: ['电气', '照明', '线路'] },
    { id: 'W007', name: '孙杰', trade: '燃气抢修', zone: 'A', level: '高级', rating: 4.9, avg_duration_min: 35, max_daily: 6, skills: ['燃气', '消防', '应急处置'] },
    { id: 'W008', name: '周敏', trade: '综合维修', zone: 'B', level: '中级', rating: 4.5, avg_duration_min: 110, max_daily: 8, skills: ['门禁', '土建', '其他', '五金'] },
    { id: 'W009', name: '吴芳', trade: '保洁', zone: 'C', level: '中级', rating: 4.3, avg_duration_min: 60, max_daily: 10, skills: ['环境', '清理', '消杀'] },
    { id: 'W010', name: '郑海', trade: '秩序维护', zone: 'A', level: '高级', rating: 4.6, avg_duration_min: 40, max_daily: 8, skills: ['消防', '秩序', '应急'] },
    { id: 'W011', name: '马超', trade: '土建维修', zone: 'B', level: '中级', rating: 4.4, avg_duration_min: 180, max_daily: 5, skills: ['土建', '墙面', '地砖'] },
    { id: 'W012', name: '何静', trade: '综合维修', zone: 'C', level: '中级', rating: 4.6, avg_duration_min: 95, max_daily: 8, skills: ['门禁', '土建', '其他'] }
  ];

  var TYPES = ['水管漏水', '下水道堵塞', '电路故障', '电梯故障', '门禁故障', '公共照明', '墙面地面破损', '环境卫生'];

  var RULES = [
    { kws: ['电梯', '困人', '被困'], type: '电梯困人', facility: '电梯', urgency: '紧急', sla: 30, trade: '电梯维保', worker: 0 },
    { kws: ['电梯'], type: '电梯故障', facility: '电梯', urgency: '高', sla: 60, trade: '电梯维保', worker: 0 },
    { kws: ['爆裂', '爆管', '喷水'], type: '水管爆裂', facility: '给排水', urgency: '紧急', sla: 30, trade: '水电工', worker: 2 },
    { kws: ['漏水', '渗水', '停水'], type: '水管漏水', facility: '给排水', urgency: '高', sla: 120, trade: '水电工', worker: 2 },
    { kws: ['下水道', '堵塞', '返水', '污水'], type: '下水道堵塞', facility: '给排水', urgency: '中', sla: 120, trade: '水电工', worker: 3 },
    { kws: ['燃气', '气味'], type: '燃气泄漏', facility: '燃气', urgency: '紧急', sla: 15, trade: '燃气抢修', worker: 6 },
    { kws: ['消防', '烟感', '灭火器'], type: '消防隐患', facility: '消防', urgency: '紧急', sla: 15, trade: '秩序维护', worker: 9 },
    { kws: ['电路', '跳闸', '停电', '短路'], type: '电路故障', facility: '电气', urgency: '高', sla: 60, trade: '电工', worker: 4 },
    { kws: ['门禁', '门锁', '道闸'], type: '门禁故障', facility: '门禁', urgency: '中', sla: 240, trade: '综合维修', worker: 7 },
    { kws: ['灯', '照明', '路灯'], type: '公共照明', facility: '照明', urgency: '中', sla: 240, trade: '电工', worker: 5 },
    { kws: ['墙面', '地面', '瓷砖', '破损'], type: '墙面地面破损', facility: '土建', urgency: '低', sla: 480, trade: '土建维修', worker: 10 },
    { kws: ['垃圾', '卫生', '保洁', '异味'], type: '环境卫生', facility: '环境', urgency: '低', sla: 480, trade: '保洁', worker: 8 }
  ];

  var STATUS_META = {
    submitted: { label: '已提交', color: '#8c8c8c' },
    understood: { label: '已理解', color: '#597ef7' },
    assigned: { label: '待接单', color: '#fa8c16' },
    accepted: { label: '已接单', color: '#13c2c2' },
    processing: { label: '处理中', color: '#2f54eb' },
    completed: { label: '已完工', color: '#722ed1' },
    returned: { label: '退回整改', color: '#ff4d4f' },
    closed: { label: '已闭环', color: '#52c41a' },
    cancelled: { label: '已取消', color: '#bfbfbf' }
  };

  var URGENCY_COLOR = { 紧急: '#ff4d4f', 高: '#fa8c16', 中: '#fadb14', 低: '#52c41a' };

  /* ------------------------------ 工具函数 ------------------------------ */
  function pad(n) { return String(n).padStart(2, '0'); }
  function fmt(d) {
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' +
      pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }
  function dateKey(d) {
    return '' + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate());
  }
  function daysAgo(n, h, m) {
    var d = new Date();
    d.setDate(d.getDate() - (n || 0));
    d.setHours(h == null ? 10 : h, m == null ? 0 : m, 0, 0);
    return fmt(d);
  }
  function minsAgo(m) {
    return fmt(new Date(Date.now() - m * 60000));
  }
  function rnd(seed) {
    var x = Math.sin(seed * 12.9898) * 43758.5453;
    return x - Math.floor(x);
  }
  var TODAY_KEY = dateKey(new Date());
  function ticketId(n) { return 'WO' + TODAY_KEY + String(n).padStart(4, '0'); }

  function clone(obj) { return JSON.parse(JSON.stringify(obj)); }

  /* ------------------------------ 趋势 / 汇总 ------------------------------ */
  function makeTrend(days) {
    var list = [];
    for (var i = 0; i < days; i++) {
      var d = new Date();
      d.setDate(d.getDate() - (days - 1 - i));
      var orders = 12 + Math.round(rnd(i + 3) * 20);
      var timeout = Math.round(rnd(i + 37) * 4);
      var closed = Math.max(2, orders - timeout - (rnd(i + 71) > 0.82 ? 1 : 0));
      list.push({
        date: d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()),
        orders: orders, closed: closed, timeout: timeout
      });
    }
    return list;
  }

  var TYPES_DATA = [
    { name: '水管漏水', value: 436 }, { name: '下水道堵塞', value: 388 },
    { name: '电路故障', value: 352 }, { name: '电梯故障', value: 301 },
    { name: '门禁故障', value: 268 }, { name: '公共照明', value: 214 },
    { name: '墙面地面破损', value: 173 }, { name: '环境卫生', value: 121 }
  ];

  var BUILDINGS_DATA = [
    { name: '5栋', value: 226, timeout_rate: 10.2 }, { name: '3栋', value: 205, timeout_rate: 8.4 },
    { name: '8栋', value: 188, timeout_rate: 7.1 }, { name: '1栋', value: 172, timeout_rate: 6.3 },
    { name: '6栋', value: 158, timeout_rate: 5.8 }, { name: '2栋', value: 141, timeout_rate: 5.1 },
    { name: '9栋', value: 126, timeout_rate: 4.6 }, { name: '4栋', value: 112, timeout_rate: 3.9 },
    { name: '7栋', value: 98, timeout_rate: 3.4 }, { name: '10栋', value: 84, timeout_rate: 2.8 }
  ];

  var WORKERS_DATA = [
    { name: '王建国', trade: '水电工', orders: 300, avg_duration: 72.0, satisfaction: 4.7, timeout_rate: 5.1 },
    { name: '张伟', trade: '电梯维保', orders: 268, avg_duration: 45.0, satisfaction: 4.8, timeout_rate: 3.2 },
    { name: '陈明', trade: '电工', orders: 246, avg_duration: 65.0, satisfaction: 4.6, timeout_rate: 4.8 },
    { name: '李强', trade: '电梯维保', orders: 224, avg_duration: 52.0, satisfaction: 4.6, timeout_rate: 4.1 },
    { name: '赵磊', trade: '水电工', orders: 208, avg_duration: 88.0, satisfaction: 4.5, timeout_rate: 7.6 },
    { name: '孙杰', trade: '燃气抢修', orders: 182, avg_duration: 35.0, satisfaction: 4.9, timeout_rate: 2.1 },
    { name: '周敏', trade: '综合维修', orders: 164, avg_duration: 110.0, satisfaction: 4.5, timeout_rate: 8.9 },
    { name: '刘洋', trade: '电工', orders: 148, avg_duration: 82.0, satisfaction: 4.4, timeout_rate: 9.2 },
    { name: '郑海', trade: '秩序维护', orders: 126, avg_duration: 40.0, satisfaction: 4.6, timeout_rate: 3.8 },
    { name: '马超', trade: '土建维修', orders: 98, avg_duration: 180.0, satisfaction: 4.4, timeout_rate: 12.4 }
  ];

  var URGENCY_DATA = [
    { name: '紧急', value: 380 }, { name: '高', value: 720 },
    { name: '中', value: 1250 }, { name: '低', value: 650 }
  ];

  var FACILITIES_DATA = [
    { name: '给排水', value: 812 }, { name: '电气', value: 640 }, { name: '电梯', value: 512 },
    { name: '门禁', value: 346 }, { name: '照明', value: 298 }, { name: '土建', value: 233 },
    { name: '环境', value: 159 }
  ];

  var HOURLY_PATTERN = [4, 2, 1, 1, 2, 5, 9, 18, 30, 44, 47, 39, 27, 31, 41, 46, 42, 33, 25, 19, 15, 11, 8, 5];

  function makeHourly() {
    return HOURLY_PATTERN.map(function (v, i) { return { hour: i, orders: v + Math.round(rnd(i + 5) * 4) }; });
  }

  /* ------------------------------ 工单样本 ------------------------------ */
  function baseTicket(over) {
    var t = {
      id: ticketId(1), trace_id: 'TRC-' + ticketId(1),
      title: '报修工单', description: '',
      reporter_name: '王女士', reporter_phone: '138****0000',
      repair_type: '其他报修', facility_type: '其他', building: '5栋', unit: '1单元', room: '',
      location: '5栋1单元', urgency: '中', urgency_score: 0.62,
      status: 'submitted', worker_id: null, worker_name: null, trade: null,
      priority: 'P3', sla_minutes: 240, deadline: null,
      auto_dispatched: false, reassigned: false, escalated: false, is_demo: false,
      created_at: minsAgo(30), understood_at: null, assigned_at: null, accepted_at: null,
      completed_at: null, closed_at: null,
      response_minutes: null, duration_minutes: null, is_timeout: false,
      photos_count: 0, result_note: '', verification_verdict: null, verification_note: null,
      satisfaction: null, updated_at: minsAgo(30)
    };
    Object.keys(over || {}).forEach(function (k) { t[k] = over[k]; });
    var meta = STATUS_META[t.status] || STATUS_META.submitted;
    t.status_label = t.status_label || meta.label;
    t.status_color = t.status_color || meta.color;
    t.urgency_color = t.urgency_color || URGENCY_COLOR[t.urgency] || '#8c8c8c';
    if (!t.deadline && t.created_at) {
      var d = new Date(t.created_at.replace(/-/g, '/'));
      d.setMinutes(d.getMinutes() + (t.sla_minutes || 240));
      t.deadline = fmt(d);
    }
    return t;
  }

  function buildTickets() {
    var list = [
      baseTicket({
        id: ticketId(1), trace_id: 'TRC-' + ticketId(1), title: '5栋1单元水管爆裂',
        description: '5栋1单元水管爆裂，楼道大量积水，业主家中停水，情况紧急。',
        repair_type: '水管爆裂', facility_type: '给排水', location: '5栋1单元', building: '5栋', unit: '1单元',
        urgency: '紧急', urgency_score: 0.96, status: 'closed', worker_id: 'W003', worker_name: '王建国', trade: '水电工',
        priority: 'P1', sla_minutes: 30, auto_dispatched: true, created_at: daysAgo(0, 9, 12),
        understood_at: daysAgo(0, 9, 12), assigned_at: daysAgo(0, 9, 13), accepted_at: daysAgo(0, 9, 20),
        completed_at: daysAgo(0, 9, 46), closed_at: daysAgo(0, 9, 53),
        response_minutes: 8.0, duration_minutes: 41.0, photos_count: 2,
        result_note: '已更换爆裂管段并完成打压测试，楼道积水清理完毕，业主确认恢复供水。',
        verification_verdict: 'pass', verification_note: '完工说明完整、照片 2 张，现场无积水，验证通过。',
        satisfaction: 4.8
      }),
      baseTicket({
        id: ticketId(2), trace_id: 'TRC-' + ticketId(2), title: '电梯运行异响',
        description: '2栋电梯运行时有明显异响，关门后短暂停顿，担心存在安全隐患。',
        repair_type: '电梯故障', facility_type: '电梯', location: '2栋', building: '2栋', unit: '',
        urgency: '高', urgency_score: 0.78, status: 'processing', worker_id: 'W001', worker_name: '张伟', trade: '电梯维保',
        priority: 'P2', sla_minutes: 60, auto_dispatched: true, created_at: daysAgo(0, 10, 5),
        understood_at: daysAgo(0, 10, 5), assigned_at: daysAgo(0, 10, 6), accepted_at: daysAgo(0, 10, 12),
        response_minutes: 7.0, photos_count: 0
      }),
      baseTicket({
        id: ticketId(3), trace_id: 'TRC-' + ticketId(3), title: '厨房下水道堵塞',
        description: '3栋2单元厨房下水道堵塞，洗菜池返水，已自行疏通但效果不好。',
        repair_type: '下水道堵塞', facility_type: '给排水', location: '3栋2单元', building: '3栋', unit: '2单元',
        urgency: '中', urgency_score: 0.54, status: 'assigned', worker_id: 'W004', worker_name: '赵磊', trade: '水电工',
        priority: 'P3', sla_minutes: 120, auto_dispatched: true, created_at: daysAgo(0, 11, 20),
        understood_at: daysAgo(0, 11, 20), assigned_at: daysAgo(0, 11, 21),
        response_minutes: null, photos_count: 0
      }),
      baseTicket({
        id: ticketId(4), trace_id: 'TRC-' + ticketId(4), title: '楼道照明回路跳闸',
        description: '6栋 3 层楼道照明回路频繁跳闸，晚上出行不便，需要电工排查线路。',
        repair_type: '电路故障', facility_type: '电气', location: '6栋3层', building: '6栋', unit: '3层',
        urgency: '高', urgency_score: 0.72, status: 'returned', worker_id: 'W006', worker_name: '刘洋', trade: '电工',
        priority: 'P2', sla_minutes: 60, auto_dispatched: true, created_at: daysAgo(0, 8, 40),
        understood_at: daysAgo(0, 8, 40), assigned_at: daysAgo(0, 8, 41), accepted_at: daysAgo(0, 8, 47),
        completed_at: daysAgo(0, 10, 2), response_minutes: 6.0, duration_minutes: 75.0,
        photos_count: 0, result_note: '已处理跳闸问题。',
        verification_verdict: 'returned', verification_note: '缺少维修过程照片，且未说明线路检测结果，请补充后重新提交。',
        is_timeout: true
      }),
      baseTicket({
        id: ticketId(5), trace_id: 'TRC-' + ticketId(5), title: '单元门禁刷卡失灵',
        description: '8栋1单元门禁刷卡没有反应，业主无法正常进出，需要尽快维修。',
        repair_type: '门禁故障', facility_type: '门禁', location: '8栋1单元', building: '8栋', unit: '1单元',
        urgency: '中', urgency_score: 0.5, status: 'closed', worker_id: 'W008', worker_name: '周敏', trade: '综合维修',
        priority: 'P3', sla_minutes: 240, auto_dispatched: true, created_at: daysAgo(1, 14, 10),
        understood_at: daysAgo(1, 14, 10), assigned_at: daysAgo(1, 14, 11), accepted_at: daysAgo(1, 14, 25),
        completed_at: daysAgo(1, 15, 30), closed_at: daysAgo(1, 15, 40),
        response_minutes: 15.0, duration_minutes: 80.0, photos_count: 3,
        result_note: '更换门禁读卡器并重新授权，测试 10 张卡均正常。',
        verification_verdict: 'pass', verification_note: '照片齐全，读卡器工作正常，验证通过。', satisfaction: 4.6
      }),
      baseTicket({
        id: ticketId(6), trace_id: 'TRC-' + ticketId(6), title: '电梯困人紧急救援',
        description: '9栋电梯有业主被困在 5 至 6 层之间，请立即安排救援！',
        repair_type: '电梯困人', facility_type: '电梯', location: '9栋', building: '9栋', unit: '',
        urgency: '紧急', urgency_score: 0.99, status: 'assigned', worker_id: 'W002', worker_name: '李强', trade: '电梯维保',
        priority: 'P1', sla_minutes: 30, auto_dispatched: true, created_at: minsAgo(6),
        understood_at: minsAgo(6), assigned_at: minsAgo(5), response_minutes: null, photos_count: 0
      }),
      baseTicket({
        id: ticketId(7), trace_id: 'TRC-' + ticketId(7), title: '地库照明灯不亮',
        description: '地库 B 区有 3 盏照明灯不亮，光线较暗，存在安全风险。',
        repair_type: '公共照明', facility_type: '照明', location: '地库B区', building: '地库', unit: 'B区',
        urgency: '中', urgency_score: 0.46, status: 'closed', worker_id: 'W006', worker_name: '刘洋', trade: '电工',
        priority: 'P3', sla_minutes: 240, auto_dispatched: true, created_at: daysAgo(2, 16, 20),
        understood_at: daysAgo(2, 16, 20), assigned_at: daysAgo(2, 16, 21), accepted_at: daysAgo(2, 16, 40),
        completed_at: daysAgo(2, 17, 30), closed_at: daysAgo(2, 17, 41),
        response_minutes: 20.0, duration_minutes: 70.0, photos_count: 2,
        result_note: '更换 3 只 LED 灯管并检查线路，照明恢复正常。',
        verification_verdict: 'pass', verification_note: '完工说明与照片符合要求，验证通过。', satisfaction: 4.4
      }),
      baseTicket({
        id: ticketId(8), trace_id: 'TRC-' + ticketId(8), title: '楼道墙面破损修补',
        description: '4栋2单元 2 层楼道墙面有明显破损和脱落，需要修补并重新粉刷。',
        repair_type: '墙面地面破损', facility_type: '土建', location: '4栋2单元2层', building: '4栋', unit: '2单元',
        urgency: '低', urgency_score: 0.22, status: 'processing', worker_id: 'W011', worker_name: '马超', trade: '土建维修',
        priority: 'P4', sla_minutes: 480, auto_dispatched: true, created_at: daysAgo(3, 9, 30),
        understood_at: daysAgo(3, 9, 30), assigned_at: daysAgo(3, 9, 31), accepted_at: daysAgo(3, 10, 15),
        response_minutes: 45.0, photos_count: 0, is_timeout: true
      }),
      baseTicket({
        id: ticketId(9), trace_id: 'TRC-' + ticketId(9), title: '燃气管道异味排查',
        description: '1栋1单元楼道闻到明显燃气味，请尽快派人上门检测，居民已疏散。',
        repair_type: '燃气泄漏', facility_type: '燃气', location: '1栋1单元', building: '1栋', unit: '1单元',
        urgency: '紧急', urgency_score: 0.98, status: 'closed', worker_id: 'W007', worker_name: '孙杰', trade: '燃气抢修',
        priority: 'P1', sla_minutes: 15, auto_dispatched: true, created_at: daysAgo(4, 18, 5),
        understood_at: daysAgo(4, 18, 5), assigned_at: daysAgo(4, 18, 5), accepted_at: daysAgo(4, 18, 7),
        completed_at: daysAgo(4, 18, 26), closed_at: daysAgo(4, 18, 33),
        response_minutes: 2.0, duration_minutes: 21.0, photos_count: 2,
        result_note: '检测为燃气软管接口轻微泄漏，已更换软管并做皂液检漏，确认无泄漏。',
        verification_verdict: 'pass', verification_note: '应急响应及时，检漏结果与照片完整，验证通过。', satisfaction: 4.9
      }),
      baseTicket({
        id: ticketId(10), trace_id: 'TRC-' + ticketId(10), title: '垃圾房异味清理',
        description: '2栋楼下垃圾房异味较重，蚊虫较多，请安排保洁清理与消杀。',
        repair_type: '环境卫生', facility_type: '环境', location: '2栋垃圾房', building: '2栋', unit: '',
        urgency: '低', urgency_score: 0.2, status: 'accepted', worker_id: 'W009', worker_name: '吴芳', trade: '保洁',
        priority: 'P4', sla_minutes: 480, auto_dispatched: true, created_at: daysAgo(0, 7, 55),
        understood_at: daysAgo(0, 7, 55), assigned_at: daysAgo(0, 7, 56), accepted_at: daysAgo(0, 8, 10),
        response_minutes: 15.0, photos_count: 0
      }),
      baseTicket({
        id: ticketId(11), trace_id: 'TRC-' + ticketId(11), title: '重复报修（已取消）',
        description: '业主重复提交的报修工单，与之前工单内容重复，已取消。',
        repair_type: '其他报修', facility_type: '其他', location: '7栋', building: '7栋', unit: '',
        urgency: '中', urgency_score: 0.5, status: 'cancelled', created_at: daysAgo(2, 11, 0),
        understood_at: daysAgo(2, 11, 0), photos_count: 0
      }),
      baseTicket({
        id: ticketId(12), trace_id: 'TRC-' + ticketId(12), title: '消防通道堆放杂物',
        description: '3栋 5 层消防通道堆放纸箱杂物，存在消防隐患，请尽快清理。',
        repair_type: '消防隐患', facility_type: '消防', location: '3栋5层', building: '3栋', unit: '5层',
        urgency: '紧急', urgency_score: 0.91, status: 'submitted', created_at: minsAgo(3),
        understood_at: minsAgo(2), photos_count: 0
      })
    ];
    return list;
  }

  var TICKETS = buildTickets();

  function makeCandidates(ticket) {
    var rule = matchRule(ticket.description + ' ' + (ticket.repair_type || ''));
    var trade = ticket.trade || rule.trade;
    var pool = WORKERS.filter(function (w) { return w.trade === trade; });
    if (!pool.length) pool = WORKERS.slice(0, 3);
    var chosen = ticket.worker_id ? WORKERS.filter(function (w) { return w.id === ticket.worker_id; })[0] : pool[0];
    var items = [{ worker: chosen || pool[0], score: 92.4 }];
    pool.forEach(function (w) {
      if (chosen && w.id === chosen.id) return;
      items.push({ worker: w, score: Math.round((74 + rnd(w.id.charCodeAt(2) + 7) * 18) * 10) / 10 });
    });
    if (items.length < 3) {
      WORKERS.slice(2, 5).forEach(function (w) {
        if (items.some(function (i) { return i.worker.id === w.id; })) return;
        items.push({ worker: w, score: Math.round((68 + rnd(w.id.charCodeAt(2) + 3) * 20) * 10) / 10 });
      });
    }
    return items.slice(0, 4).sort(function (a, b) { return b.score - a.score; }).map(function (i) {
      return {
        worker_id: i.worker.id,
        name: i.worker.name,
        worker_name: i.worker.name,
        trade: i.worker.trade,
        zone: i.worker.zone,
        rating: i.worker.rating,
        active_tickets: Math.round(rnd(i.worker.id.charCodeAt(3) + 2) * 4),
        score: i.score,
        breakdown: {
          skill: 38, zone: i.worker.zone === 'A' ? 18 : 12,
          load: 14, rating: Math.round((i.worker.rating - 4) * 20), sla: 10
        }
      };
    });
  }

  function dispatchLog(ticket) {
    var candidates = makeCandidates(ticket);
    var selected = candidates[0] || {};
    return {
      agent: 'dispatch', agent_name: '派单决策 Agent', action: 'dispatch_decision',
      input_summary: '类型「' + (ticket.repair_type || '待识别') + '」，紧急度「' + (ticket.urgency || '中') + '」，区域 ' + (ticket.building || '未知'),
      output: {
        selected: { worker_id: selected.worker_id, worker_name: selected.worker_name, trade: selected.trade, score: selected.score },
        candidates: candidates,
        factors: ['技能匹配', '区域就近', '当前负载', '历史评分', 'SLA 时限'],
        sla_minutes: ticket.sla_minutes
      },
      rationale: '技能完全匹配（+' + (selected.breakdown ? selected.breakdown.skill : 38) + '）；区域就近（+' +
        (selected.breakdown ? selected.breakdown.zone : 18) + '）；当前负载较低（+' +
        (selected.breakdown ? selected.breakdown.load : 14) + '）；历史评分 ' + (selected.rating || 4.7) +
        '（+' + (selected.breakdown ? selected.breakdown.rating : 12) + '）；SLA 时限内可完成（+' +
        (selected.breakdown ? selected.breakdown.sla : 10) + '）',
      confidence: 0.93, duration_ms: 11.4, created_at: ticket.assigned_at || ticket.created_at
    };
  }

  /* ------------------------------ 事件 / 日志 ------------------------------ */
  function buildEvents(ticket) {
    var t = ticket;
    var ev = [];
    ev.push({
      event_type: 'ticket_created', actor: 'coordinator', agent: 'coordinator', agent_name: '工单编排中心',
      title: '工单创建', summary: '业主提交报修：' + t.description,
      from_status: null, to_status: 'submitted', ts: t.created_at
    });
    if (t.understood_at) {
      ev.push({
        event_type: 'understood', actor: 'understanding', agent: 'understanding', agent_name: '报修理解 Agent',
        title: '报修理解完成',
        summary: '识别类型「' + t.repair_type + '」，紧急程度「' + t.urgency + '」，置信度 0.94，生成结构化派单要素。',
        from_status: 'submitted', to_status: 'understood', ts: t.understood_at
      });
    }
    if (t.assigned_at) {
      ev.push({
        event_type: 'dispatched', actor: 'dispatch', agent: 'dispatch', agent_name: '派单决策 Agent',
        title: '智能派单完成',
        summary: '派单至 ' + t.worker_name + '（' + t.trade + '），SLA ' + t.sla_minutes + ' 分钟，综合评分 92.4。',
        from_status: 'understood', to_status: 'assigned', ts: t.assigned_at
      });
    }
    if (t.accepted_at) {
      ev.push({
        event_type: 'accepted', actor: 'monitoring', agent: 'monitoring', agent_name: '过程监控 Agent',
        title: '维修工已接单',
        summary: t.worker_name + ' 确认接单，响应 ' + (t.response_minutes || 8) + ' 分钟，进入处理阶段。',
        from_status: 'assigned', to_status: 'accepted', ts: t.accepted_at
      });
    }
    if (t.status === 'returned' || t.is_timeout) {
      ev.push({
        event_type: 'warning_created', actor: 'monitoring', agent: 'monitoring', agent_name: '过程监控 Agent',
        title: t.status === 'returned' ? '闭环验证未通过 · 退回整改' : 'SLA 超时预警',
        summary: t.status === 'returned'
          ? (t.verification_note || '完工资料不完整，工单已退回整改。')
          : '工单处理已接近 / 超过 SLA 时限，已生成超时预警并升级至物业主管。',
        from_status: 'processing', to_status: 'returned', ts: t.completed_at || minsAgo(40)
      });
    }
    if (t.completed_at) {
      ev.push({
        event_type: 'completed', actor: 'coordinator', agent: 'coordinator', agent_name: '工单编排中心',
        title: '维修工提交完工',
        summary: t.result_note || '维修工提交完工说明与现场照片。',
        from_status: 'processing', to_status: 'completed', ts: t.completed_at
      });
    }
    if (t.verification_verdict) {
      ev.push({
        event_type: t.verification_verdict === 'pass' ? 'verification_passed' : 'verification_returned',
        actor: 'verification', agent: 'verification', agent_name: '闭环验证 Agent',
        title: t.verification_verdict === 'pass' ? '闭环验证通过' : '闭环验证未通过',
        summary: t.verification_note || '验证 Agent 已完成完工资料核验。',
        from_status: 'completed', to_status: t.verification_verdict === 'pass' ? 'completed' : 'returned',
        ts: t.closed_at || t.completed_at
      });
    }
    if (t.closed_at) {
      ev.push({
        event_type: 'closed', actor: 'coordinator', agent: 'coordinator', agent_name: '工单编排中心',
        title: '工单闭环',
        summary: '工单已归档，处理时长 ' + (t.duration_minutes || 41) + ' 分钟，满意度 ' + (t.satisfaction || 4.6) + '。',
        from_status: 'completed', to_status: 'closed', ts: t.closed_at
      });
    }
    return ev.sort(function (a, b) { return String(a.ts).localeCompare(String(b.ts)); });
  }

  function buildLogs(ticket) {
    var t = ticket;
    if (!t.understood_at) return [];
    var logs = [];
    logs.push({
      agent: 'understanding', agent_name: '报修理解 Agent', action: 'ticket_understood',
      input_summary: t.description,
      output: {
        repair_type: t.repair_type, facility_type: t.facility_type,
        urgency: t.urgency, urgency_score: t.urgency_score,
        location: t.location, keyword_hits: ['水管', '爆裂', '积水'].slice(0, Math.min(3, (t.repair_type || '').length % 3 + 1)),
        structured: true
      },
      rationale: '关键词命中「' + t.repair_type + '」(+42)；位置要素完整「' + (t.location || '未知') + '」(+20)；' +
        '紧急词命中「' + t.urgency + '」(+18)；设施类型映射「' + t.facility_type + '」(+12)。',
      confidence: 0.94, duration_ms: 8.2, created_at: t.understood_at
    });
    if (t.assigned_at) {
      var d = dispatchLog(t);
      d.created_at = t.assigned_at;
      d.ticket_id = t.id;
      logs.push(d);
    }
    if (t.is_timeout || t.status === 'returned') {
      logs.push({
        agent: 'monitoring', agent_name: '过程监控 Agent', action: 'sla_scan',
        input_summary: '工单 ' + t.id + ' · SLA ' + t.sla_minutes + ' 分钟 · 当前状态 ' + (t.status_label || t.status),
        output: {
          scanned: 1, warnings_created: 1, escalated: 1,
          warning_type: 'processing_timeout', level: 'high',
          deadline: t.deadline, message: '处理进度已超 SLA 时限，建议人工介入并升级。'
        },
        rationale: '剩余时限低于预警阈值（提前 10 分钟）；维修工负载偏高；历史同类型工单超时率 8.8%，生成超时预警。',
        confidence: 0.89, duration_ms: 6.5, created_at: t.completed_at || minsAgo(35)
      });
    }
    if (t.verification_verdict) {
      logs.push({
        agent: 'verification', agent_name: '闭环验证 Agent', action: 'completion_verified',
        input_summary: '完工说明：' + (t.result_note || '—') + '；现场照片 ' + (t.photos_count || 0) + ' 张',
        output: {
          verdict: t.verification_verdict,
          pass: t.verification_verdict === 'pass',
          photos_count: t.photos_count || 0,
          checks: {
            note_complete: !!(t.result_note && t.result_note.length > 10),
            photos_enough: (t.photos_count || 0) >= 1,
            description_match: t.verification_verdict === 'pass'
          },
          note: t.verification_note
        },
        rationale: t.verification_verdict === 'pass'
          ? '完工说明长度与要素完整（+30）；现场照片数量满足要求（+25）；描述与报修内容一致（+25）；历史同类验收通过率 88.5%（+10）。'
          : '缺少维修过程照片（-25）；完工说明未覆盖检测结果（-15）；证据不足，判定退回整改。',
        confidence: t.verification_verdict === 'pass' ? 0.92 : 0.84,
        duration_ms: 9.7,
        created_at: t.closed_at || t.completed_at
      });
    }
    if (t.closed_at) {
      logs.push({
        agent: 'coordinator', agent_name: '工单编排中心', action: 'trace_closed',
        input_summary: '工单 ' + t.id + ' 全链路事件 ' + buildEvents(t).length + ' 条',
        output: {
          ticket_id: t.id, trace_id: t.trace_id, final_status: 'closed',
          duration_minutes: t.duration_minutes, satisfaction: t.satisfaction,
          closed_by: 'verification_agent'
        },
        rationale: '闭环验证通过；所有事件与决策日志已归档；运行指标已同步至看板与分析库。',
        confidence: 0.98, duration_ms: 3.1, created_at: t.closed_at
      });
    }
    return logs;
  }

  var LOG_SEQ = 1;
  function allLogs() {
    var items = [];
    TICKETS.forEach(function (t) {
      buildLogs(t).forEach(function (log) {
        items.push({
          id: LOG_SEQ++, trace_id: t.trace_id, ticket_id: t.id,
          agent: log.agent, agent_name: log.agent_name, action: log.action,
          input_summary: log.input_summary, output: log.output,
          rationale: log.rationale, confidence: log.confidence,
          duration_ms: log.duration_ms, created_at: log.created_at
        });
      });
    });
    return items.sort(function (a, b) { return String(b.created_at).localeCompare(String(a.created_at)) || b.id - a.id; });
  }

  function buildWarnings(ticket) {
    var list = [];
    if (ticket.is_timeout) {
      list.push({
        id: 1, ticket_id: ticket.id, level: 'high', warning_type: 'processing_timeout',
        message: '工单处理已超出 SLA 时限，已升级至物业主管跟进。',
        status: 'open', created_at: ticket.completed_at || minsAgo(45),
        escalated_to: '物业主管'
      });
    }
    if (ticket.status === 'returned') {
      list.push({
        id: 2, ticket_id: ticket.id, level: 'medium', warning_type: 'verification_returned',
        message: ticket.verification_note || '闭环验证未通过，工单退回整改。',
        status: 'open', created_at: ticket.completed_at || minsAgo(30)
      });
    }
    return list;
  }

  function ticketDetail(id, hint) {
    var t = (hint && hint.id === id) ? hint : TICKETS.filter(function (x) { return x.id === id; })[0];
    if (!t) return null;
    var tk = clone(t);
    return {
      ticket: tk,
      events: buildEvents(tk),
      logs: buildLogs(tk),
      warnings: buildWarnings(tk)
    };
  }

  /* ------------------------------ 派单模拟 ------------------------------ */
  function matchRule(text) {
    var txt = text || '';
    for (var i = 0; i < RULES.length; i++) {
      var r = RULES[i];
      for (var j = 0; j < r.kws.length; j++) {
        if (txt.indexOf(r.kws[j]) >= 0) return r;
      }
    }
    return { type: '其他报修', facility: '其他', urgency: '中', sla: 240, trade: '综合维修', worker: 7 };
  }

  function simulateDispatch(payload) {
    payload = payload || {};
    var description = payload.description || '电梯困人，业主被困在 5 至 6 层之间，请立即救援。';
    var rule = matchRule(description);
    var worker = WORKERS[rule.worker] || WORKERS[0];
    var now = new Date();
    var id = 'WO' + dateKey(now) + String(Math.floor(Math.random() * 9000) + 1000);
    var assigned = fmt(new Date(now.getTime() + 60000));
    var deadline = fmt(new Date(now.getTime() + (rule.sla + 1) * 60000));
    var ticket = baseTicket({
      id: id, trace_id: 'TRC-' + id, title: description.slice(0, 26),
      description: description, reporter_name: payload.reporter_name || '王女士',
      reporter_phone: payload.reporter_phone || '138****0000',
      repair_type: rule.type, facility_type: rule.facility,
      building: payload.building || rule.type.slice(0, 1) + '栋', unit: payload.unit || '1单元',
      location: (payload.building || '5栋') + (payload.unit || '1单元'),
      urgency: rule.urgency, urgency_score: rule.urgency === '紧急' ? 0.96 : rule.urgency === '高' ? 0.78 : 0.55,
      status: 'assigned', worker_id: worker.id, worker_name: worker.name, trade: rule.trade,
      priority: rule.urgency === '紧急' ? 'P1' : rule.urgency === '高' ? 'P2' : 'P3',
      sla_minutes: rule.sla, auto_dispatched: true, created_at: fmt(now),
      understood_at: fmt(now), assigned_at: assigned, response_minutes: null, photos_count: 0
    });
    ticket.deadline = deadline;
    return {
      ticket: ticket,
      events: buildEvents(ticket),
      logs: buildLogs(ticket),
      warnings: buildWarnings(ticket)
    };
  }

  function demoDetail() {
    var run = demoRun();
    var ticket = run.ticket;
    var baseTs = new Date(String(ticket.created_at).replace(/-/g, '/')).getTime();
    var events = run.steps.map(function (step) {
      var m = /T\+(\d{2}):(\d{2})/.exec(step.time) || [];
      var offsetSec = m.length ? Number(m[1]) * 60 + Number(m[2]) : 0;
      return {
        event_type: step.event_type, actor: step.agent, agent: step.agent,
        agent_name: step.agent_name, title: step.title, summary: step.summary,
        from_status: null, to_status: step.status, ts: fmt(new Date(baseTs + offsetSec * 1000))
      };
    });
    return {
      ticket: ticket,
      events: events,
      logs: buildLogs(ticket),
      warnings: [],
      is_sample: true
    };
  }

  /* ------------------------------ 完整演示 ------------------------------ */
  function demoRun() {
    var now = new Date();
    var id = 'WO' + dateKey(now) + '9001';
    var t0 = now.getTime();
    function tm(offsetSec) {
      var d = new Date(t0 + offsetSec * 1000);
      return fmt(d);
    }
    var steps = [
      {
        seq: 1, time: 'T+00:00', agent: 'coordinator', agent_name: '工单编排中心',
        event_type: 'ticket_created', title: '工单创建',
        summary: '业主提交报修：5栋1单元水管爆裂，楼道大量积水，业主家中停水。生成 trace_id 与工单号。',
        status: 'submitted', status_label: '已提交', payload: { ticket_id: id }
      },
      {
        seq: 2, time: 'T+00:01', agent: 'understanding', agent_name: '报修理解 Agent',
        event_type: 'understood', title: '报修理解完成',
        summary: '识别类型「水管爆裂」、设施「给排水」、紧急程度「紧急」、位置「5栋1单元」，置信度 0.94。',
        status: 'understood', status_label: '已理解', payload: { repair_type: '水管爆裂', urgency: '紧急', confidence: 0.94 }
      },
      {
        seq: 3, time: 'T+00:02', agent: 'dispatch', agent_name: '派单决策 Agent',
        event_type: 'dispatched', title: '智能派单完成',
        summary: '技能匹配水电工 2 人，选中王建国（高级、区域 A、当前负载 2/8），综合评分 92.4，SLA 30 分钟。',
        status: 'assigned', status_label: '待接单', payload: { worker_name: '王建国', trade: '水电工', score: 92.4 }
      },
      {
        seq: 4, time: 'T+03:10', agent: 'coordinator', agent_name: '工单编排中心',
        event_type: 'accepted', title: '维修工已接单',
        summary: '王建国确认接单，响应耗时 3 分 10 秒，在紧急工单 10 分钟接单时限内。',
        status: 'accepted', status_label: '已接单', payload: { response_minutes: 3.2 }
      },
      {
        seq: 5, time: 'T+12:30', agent: 'monitoring', agent_name: '过程监控 Agent',
        event_type: 'warning_created', title: 'SLA 预警扫描',
        summary: '扫描 1 条在途工单：剩余时限 17.5 分钟，无超时风险；维修工已到达现场并开始处理，生成过程记录。',
        status: 'processing', status_label: '处理中', payload: { scanned: 1, warnings_created: 0, escalated: 0 }
      },
      {
        seq: 6, time: 'T+28:40', agent: 'coordinator', agent_name: '工单编排中心',
        event_type: 'completed', title: '维修工提交完工',
        summary: '更换爆裂管段并完成打压测试，楼道积水清理完毕；提交完工说明与 2 张现场照片。',
        status: 'completed', status_label: '已完工', payload: { result_note: '更换管段并打压测试完成', photos_count: 2 }
      },
      {
        seq: 7, time: 'T+28:52', agent: 'verification', agent_name: '闭环验证 Agent',
        event_type: 'verification_passed', title: '闭环验证通过',
        summary: '完工说明完整、现场照片 2 张、描述与报修内容一致，判定验证通过，置信度 0.92。',
        status: 'completed', status_label: '验证通过', payload: { verdict: 'pass', confidence: 0.92 }
      },
      {
        seq: 8, time: 'T+28:55', agent: 'coordinator', agent_name: '工单编排中心',
        event_type: 'closed', title: '工单闭环',
        summary: '工单归档闭环，总处理时长 28.9 分钟（SLA 30 分钟），沉淀 Agent 决策日志 4 条。',
        status: 'closed', status_label: '已闭环', payload: { duration_minutes: 28.9 }
      }
    ];
    var ticket = baseTicket({
      id: id, trace_id: 'TRC-' + id, title: '5栋1单元水管爆裂',
      description: '5栋1单元水管爆裂，楼道大量积水，业主家中停水。',
      repair_type: '水管爆裂', facility_type: '给排水', location: '5栋1单元', building: '5栋', unit: '1单元',
      urgency: '紧急', urgency_score: 0.96, status: 'closed', worker_id: 'W003', worker_name: '王建国', trade: '水电工',
      priority: 'P1', sla_minutes: 30, auto_dispatched: true, created_at: tm(0),
      understood_at: tm(1), assigned_at: tm(2), accepted_at: tm(190),
      completed_at: tm(1720), closed_at: tm(1735),
      response_minutes: 3.2, duration_minutes: 28.9, photos_count: 2,
      result_note: '更换爆裂管段并完成打压测试，楼道积水清理完毕，业主确认恢复供水。',
      verification_verdict: 'pass', verification_note: '完工说明完整、照片 2 张，现场无积水，验证通过。',
      satisfaction: 4.9, is_demo: true
    });
    return {
      ticket: ticket,
      steps: steps,
      metrics_before: {
        closed_rate: 94.2, timeout_rate: 8.8, avg_duration: 128.4,
        agent_avg_decision_ms: 12.3
      },
      metrics_after: {
        closed_rate: 94.2, timeout_rate: 8.8, avg_duration: 127.9,
        agent_avg_decision_ms: 12.2
      },
      warnings: [],
      case: {
        title: '5栋1单元水管爆裂', sla_minutes: 30, worker: '王建国',
        trade: '水电工', difficulty: '简单', total_minutes: 28.9,
        summary: '紧急给排水工单，30 分钟 SLA 内完成派单、接单、处理、验证与闭环。'
      }
    };
  }

  /* ------------------------------ 对外接口 ------------------------------ */
  window.SampleData = {
    summary: function (days) {
      var d = days || 30;
      return {
        generated_at: fmt(new Date()),
        is_sample: true,
        historical: {
          total: 3000, closed_rate: 94.2, timeout_rate: 8.8, avg_duration: 128.4,
          avg_response: 16.2, satisfaction: 4.42, auto_dispatch_rate: 93.6, period_days: 180
        },
        live: {
          total: 12, open: 5, closed: 6, today: 3, timeout: 2, returned: 1,
          escalated: 1, closed_rate: 50.0
        },
        agent: {
          understanding_accuracy: 94.7, dispatch_accuracy: 91.8,
          verification_pass_rate: 88.5, manual_reassign_rate: 6.2, avg_decision_ms: 12.3
        },
        trend: makeTrend(d),
        types: clone(TYPES_DATA),
        facilities: clone(FACILITIES_DATA),
        buildings: clone(BUILDINGS_DATA),
        workers: clone(WORKERS_DATA),
        urgency: clone(URGENCY_DATA),
        hourly: makeHourly(),
        recent: TICKETS.slice()
          .sort(function (a, b) { return String(b.created_at).localeCompare(String(a.created_at)); })
          .slice(0, 8)
          .map(function (t) {
            return {
              id: t.id, repair_type: t.repair_type, status: t.status, status_label: t.status_label,
              urgency: t.urgency, location: t.location, worker_name: t.worker_name, created_at: t.created_at
            };
          }),
        insights: [
          '历史闭环率 94.2%，超时率 8.8%，整体流转健康；紧急工单建议持续保持 30 分钟 SLA。',
          '水管类报修占比最高（给排水设施 812 单），建议在 5 栋、3 栋增配水电工值班力量。',
          '自动派单准确率 93.6%，人工改派率 6.2%，主要发生在跨工种与低紧急度工单。',
          '维修工效率 Top3：孙杰、张伟、王建国，平均处理时长均低于全局均值 128.4 分钟。'
        ]
      };
    },

    ticketsPage: function (params) {
      params = params || {};
      var items = TICKETS.slice();
      if (params.status && params.status !== 'all') {
        if (params.status === 'open') {
          items = items.filter(function (t) {
            return ['submitted', 'understood', 'assigned', 'accepted', 'processing', 'completed', 'returned'].indexOf(t.status) >= 0;
          });
        } else {
          items = items.filter(function (t) { return t.status === params.status; });
        }
      }
      if (params.urgency) items = items.filter(function (t) { return t.urgency === params.urgency; });
      if (params.q) {
        var q = String(params.q).toLowerCase();
        items = items.filter(function (t) {
          return [t.id, t.description, t.location, t.worker_name, t.repair_type].join(' ').toLowerCase().indexOf(q) >= 0;
        });
      }
      items.sort(function (a, b) { return String(b.created_at).localeCompare(String(a.created_at)); });
      var limit = params.limit || 20;
      var offset = params.offset || 0;
      return {
        items: clone(items.slice(offset, offset + limit)),
        total: items.length, limit: limit, offset: offset, is_sample: true
      };
    },

    ticketDetail: function (id) {
      var d = ticketDetail(id);
      d.is_sample = true;
      return d;
    },

    logs: function (params) {
      params = params || {};
      var items = allLogs();
      if (params.agent) items = items.filter(function (l) { return l.agent === params.agent; });
      if (params.q) {
        var q = String(params.q).toLowerCase();
        items = items.filter(function (l) {
          return [l.agent_name, l.action, l.input_summary, l.rationale, l.ticket_id].join(' ').toLowerCase().indexOf(q) >= 0;
        });
      }
      var limit = params.limit || 100;
      var offset = params.offset || 0;
      return { items: clone(items.slice(offset, offset + limit)), total: items.length, is_sample: true };
    },

    agentEval: function () {
      return {
        is_sample: true,
        methodology: '基于 3000 条历史工单与在线决策日志的离线回放评估，覆盖理解准确率、派单准确率与闭环验收通过率。',
        sample_size: 3000,
        metrics: {
          understanding_accuracy: 94.7, dispatch_accuracy: 91.8,
          verification_pass_rate: 88.5, manual_reassign_rate: 6.2, avg_decision_ms: 12.3
        },
        per_type: TYPES_DATA.map(function (t, i) {
          return {
            repair_type: t.name, sample: Math.round(t.value * 0.4),
            understanding_accuracy: Math.round((90 + rnd(i + 2) * 8) * 10) / 10,
            dispatch_accuracy: Math.round((86 + rnd(i + 12) * 11) * 10) / 10,
            avg_duration: Math.round(60 + rnd(i + 22) * 120)
          };
        }),
        confusion_matrix: {
          labels: ['水管', '电气', '电梯', '门禁', '其他'],
          matrix: [[186, 4, 2, 1, 3], [5, 152, 1, 2, 4], [1, 2, 118, 0, 3], [2, 3, 0, 96, 5], [4, 5, 2, 6, 74]]
        },
        insights: [
          '理解准确率 94.7%，其中水管、电梯类识别最稳定；门禁与公共照明易混淆。',
          '派单准确率 91.8%，跨工种工单建议增加二次确认规则。',
          '闭环验收通过率 88.5%，退回原因集中在完工照片缺失（约 42%）。',
          '平均决策耗时 12.3 ms，远低于 50 ms 的交互体验阈值。'
        ]
      };
    },

    typeStats: function () {
      return {
        is_sample: true,
        types: TYPES_DATA.map(function (t, i) {
          return {
            name: t.name, count: t.value,
            avg_duration: Math.round(70 + rnd(i + 32) * 110),
            timeout_rate: Math.round((3 + rnd(i + 42) * 9) * 10) / 10,
            sla_minutes: [120, 120, 60, 60, 240, 240, 480, 480][i]
          };
        }),
        monthly: [
          { month: '2026-05', orders: 380, closed_rate: 92.1, timeout_rate: 10.2 },
          { month: '2026-06', orders: 412, closed_rate: 93.4, timeout_rate: 9.4 },
          { month: '2026-07', orders: 468, closed_rate: 93.8, timeout_rate: 9.0 },
          { month: '2026-08', orders: 504, closed_rate: 94.1, timeout_rate: 8.7 },
          { month: '2026-09', orders: 476, closed_rate: 94.6, timeout_rate: 8.3 },
          { month: '2026-10', orders: 488, closed_rate: 95.0, timeout_rate: 7.9 }
        ],
        workers: clone(WORKERS_DATA)
      };
    },

    simulateDispatch: function (payload) {
      var d = simulateDispatch(payload);
      d.is_sample = true;
      return d;
    },

    demoRun: function () {
      var d = demoRun();
      d.is_sample = true;
      return d;
    },

    demoDetail: function () {
      return demoDetail();
    },

    workers: function () { return { items: clone(WORKERS) }; }
  };
})();
