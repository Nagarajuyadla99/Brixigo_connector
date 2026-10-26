/* Brixigo Cloud Admin — SPA client */
const API = '/api/v1';
const TOKEN_KEY = 'brixigo_cloud_token';
const USER_KEY = 'brixigo_cloud_user';

let charts = {};
let currentModule = 'dashboard';
let liveRefreshTimer = null;
const LIVE_REFRESH_MS = 10000;
const LIVE_MODULES = new Set(['dashboard', 'monitoring', 'connectors', 'devices', 'attendance']);

function stopLiveRefresh() {
  if (liveRefreshTimer) {
    clearInterval(liveRefreshTimer);
    liveRefreshTimer = null;
  }
}

function startLiveRefresh(module) {
  stopLiveRefresh();
  if (!LIVE_MODULES.has(module)) return;
  liveRefreshTimer = setInterval(() => {
    if (currentModule !== module || !getToken()) return;
    const loader = loaders[module];
    if (!loader) return;
    loader().catch(err => console.error('live-refresh', module, err));
  }, LIVE_REFRESH_MS);
}

function getToken() { return localStorage.getItem(TOKEN_KEY); }
function getUser() { try { return JSON.parse(localStorage.getItem(USER_KEY) || '{}'); } catch { return {}; } }

function setAuth(token, user) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

async function defaultOrganisationName() {
  const user = getUser();
  if (user.organisation_name) return user.organisation_name;
  const orgs = await api('/tenancy/organisations');
  return orgs[0]?.name || '';
}

function clearAuth() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  const token = getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(`${API}${path}`, { ...opts, headers });
  if (res.status === 401) { clearAuth(); showLogin(); throw new Error('Unauthorized'); }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'Request failed');
  }
  if (res.headers.get('content-type')?.includes('json')) return res.json();
  return res;
}

function showLogin() {
  document.getElementById('login-screen').classList.remove('hidden');
  document.getElementById('app-shell').classList.add('hidden');
}

function showApp() {
  document.getElementById('login-screen').classList.add('hidden');
  document.getElementById('app-shell').classList.remove('hidden');
  const user = getUser();
  document.getElementById('user-info').textContent = `${user.full_name || user.email} · ${user.role || ''}`;
  refreshConnectorServerStatus().catch(() => {});
}

function statusPill(status) {
  const s = (status || '').toLowerCase();
  let cls = 'status-offline';
  if (['online', 'healthy', 'active', 'completed', 'connected', 'valid', 'synced', 'in'].includes(s)) cls = 'status-online';
  else if (['warning', 'degraded', 'queued', 'slow', 'pending', 'delayed', 'out', 'break_out', 'ot_out'].includes(s)) cls = 'status-warning';
  else if (['critical', 'failed', 'offline', 'disconnected', 'tls_error', 'timeout', 'unavailable', 'auth_error'].includes(s)) cls = 'status-critical';
  return `<span class="status-pill ${cls}">${status || '—'}</span>`;
}

function renderTable(el, headers, rows) {
  if (!rows.length) { el.innerHTML = '<p class="hint">No data.</p>'; return; }
  const th = headers.map(h => `<th>${h.label}</th>`).join('');
  const tr = rows.map(r => `<tr>${headers.map(h => {
    const cls = h.cellClass ? ` class="${h.cellClass}"` : '';
    const content = h.render ? h.render(r) : (r[h.key] ?? '—');
    return `<td${cls}>${content}</td>`;
  }).join('')}</tr>`).join('');
  el.innerHTML = `<thead><tr>${th}</tr></thead><tbody>${tr}</tbody>`;
}

function renderStats(container, stats) {
  container.innerHTML = stats.map(s => `
    <div class="stat-card">
      <div class="label">${s.label}</div>
      <div class="value">${s.value}</div>
    </div>
  `).join('');
}

function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

function makeChart(id, type, labels, data, label) {
  destroyChart(id);
  const ctx = document.getElementById(id);
  if (!ctx) return;
  charts[id] = new Chart(ctx, {
    type,
    data: {
      labels,
      datasets: [{
        label,
        data,
        backgroundColor: type === 'line' ? 'rgba(15,76,92,0.15)' : ['#0f4c5c','#1a6b7d','#d97706','#dc2626','#64748b'],
        borderColor: '#0f4c5c',
        borderWidth: 2,
        fill: type === 'line',
        tension: 0.3,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: type !== 'line' } },
      scales: type === 'line' ? { y: { beginAtZero: true } } : {},
    },
  });
}

function formatDateTimeAligned(value) {
  let d;
  if (value instanceof Date) {
    d = value;
  } else {
    const raw = String(value || '').trim();
    if (!raw) return '—';
    // SQLite/UTC naive "YYYY-MM-DD HH:MM:SS" → treat as UTC for stable display
    const normalized = (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(raw) && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(raw))
      ? raw.replace(' ', 'T') + 'Z'
      : raw;
    d = new Date(normalized);
  }
  if (Number.isNaN(d.getTime())) return String(value || '').trim() || '—';
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getDate())} ${months[d.getMonth()]} ${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function formatPunchTime(value, collectedAt) {
  const raw = String(value || '').trim();
  const parsed = raw ? new Date(raw) : null;
  if (parsed && !Number.isNaN(parsed.getTime()) && parsed.getFullYear() >= 2010) {
    return formatDateTimeAligned(parsed);
  }
  const collected = collectedAt ? new Date(
    (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(String(collectedAt)) && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(String(collectedAt)))
      ? String(collectedAt).replace(' ', 'T') + 'Z'
      : collectedAt
  ) : null;
  if (collected && !Number.isNaN(collected.getTime())) {
    return formatDateTimeAligned(collected);
  }
  return raw || '—';
}

function isConnectorOnline(connector) {
  const status = String(connector?.status || '').toLowerCase();
  if (['online', 'running', 'connected', 'healthy', 'up', 'ok'].includes(status)) return true;
  if (!connector?.last_heartbeat_at) return false;
  const hb = new Date(connector.last_heartbeat_at).getTime();
  if (Number.isNaN(hb)) return false;
  return (Date.now() - hb) < 120000;
}

async function refreshConnectorServerStatus(connectors) {
  let rows = connectors;
  if (!rows) {
    try {
      rows = await api('/connectors');
    } catch {
      rows = [];
    }
  }
  const list = Array.isArray(rows) ? rows : [];
  const online = list.filter(isConnectorOnline);
  const primary = online[0] || list[0];
  const nameEl = document.getElementById('connector-server-name');
  const pillEl = document.getElementById('connector-server-pill');
  const badge = document.getElementById('status-badge');
  if (nameEl) {
    nameEl.textContent = primary
      ? `${primary.name || 'Connector'}${list.length > 1 ? ` (+${list.length - 1})` : ''}`
      : 'No connector registered';
  }
  const onlineCount = online.length;
  const label = !list.length ? 'offline' : (onlineCount ? 'online' : 'offline');
  if (pillEl) {
    pillEl.className = `status-pill ${label === 'online' ? 'status-online' : 'status-offline'}`;
    pillEl.textContent = list.length ? `${onlineCount}/${list.length} ${label}` : 'offline';
  }
  if (badge) {
    badge.className = `badge ${label === 'online' ? 'badge-ok' : 'badge-crit'}`;
    badge.textContent = label === 'online' ? 'CONNECTED' : 'DISCONNECTED';
  }
}

async function loadDashboard() {
  const [summary, connectors, devices] = await Promise.all([
    api('/analytics/summary'),
    api('/connectors'),
    api('/devices'),
  ]);
  await refreshConnectorServerStatus(connectors);
  renderStats(document.getElementById('stat-grid'), [
    { label: 'Connectors Online', value: summary.connectors_online },
    { label: 'Devices Healthy', value: summary.devices_healthy },
    { label: 'Attendance Today', value: summary.attendance_today },
    { label: 'Avg Health Score', value: summary.avg_health_score },
    { label: 'Uptime %', value: summary.uptime_percent },
    { label: 'Pending Commands', value: summary.pending_commands },
  ]);
  makeChart('chart-attendance', 'line',
    summary.attendance_week.map(d => d.date.slice(5)),
    summary.attendance_week.map(d => d.count),
    'Attendance');
  makeChart('chart-health', 'doughnut',
    ['Healthy', 'Warning', 'Critical', 'Offline'],
    [summary.devices_healthy, summary.devices_warning, summary.devices_critical, summary.devices_offline],
    'Device Health');
  const liveConnectors = (connectors || []).filter(c =>
    isConnectorOnline(c) || c.last_heartbeat_at
  );
  renderTable(document.getElementById('dash-connectors'), [
    { key: 'name', label: 'Connector Server' },
    { key: 'status', label: 'Status', render: r => statusPill(isConnectorOnline(r) ? 'online' : (r.status || 'offline')) },
    { key: 'connected_devices', label: 'Devices' },
    { key: 'queue_size', label: 'Queue' },
    { key: 'hostname', label: 'Host' },
    { key: 'last_heartbeat_at', label: 'Last Heartbeat', render: r => r.last_heartbeat_at ? new Date(r.last_heartbeat_at).toLocaleString() : '—' },
  ], liveConnectors.length ? liveConnectors : (connectors || []));
  renderTable(document.getElementById('dash-devices'), [
    { key: 'name', label: 'Device' },
    { key: 'ip_address', label: 'IP' },
    { key: 'terminal_code', label: 'Terminal' },
    { key: 'status', label: 'Status', render: r => statusPill(r.status) },
    { key: 'health_status', label: 'Health', render: r => statusPill(r.health_status) },
    { key: 'connector_name', label: 'Connector' },
  ], devices || []);
}

function punchClock(value, collectedAt) {
  const full = formatPunchTime(value, collectedAt);
  const parts = String(full).split(' ');
  return parts.length >= 4 ? parts[parts.length - 1].slice(0, 5) : full;
}

function isGeneratedEmployeeName(name, code) {
  const n = String(name || '').trim();
  const c = String(code || '').trim();
  if (!n) return true;
  if (c && n.toLowerCase() === c.toLowerCase()) return true;
  if (/^employee\s+\d+$/i.test(n)) return true;
  if (c && n.toLowerCase() === `employee ${c}`.toLowerCase()) return true;
  return false;
}

function displayPersonName(name, code) {
  if (!isGeneratedEmployeeName(name, code)) return String(name).trim();
  return code ? `Device user ${code}` : 'Unnamed employee';
}

function classificationLabel(value) {
  const key = String(value || '').trim().toLowerCase();
  if (key === 'device_reported' || key === 'device') return 'Device reported';
  if (key === 'system_inference' || key === 'system_inferred' || key === 'inferred') return 'System inferred';
  if (key === 'manual') return 'Manual';
  if (key === 'unknown') return 'Unknown';
  return '';
}

function verificationLabel(value) {
  const key = String(value || '').trim().toLowerCase();
  const labels = {
    fingerprint: 'Fingerprint',
    face: 'Face',
    palm: 'Palm',
    card: 'Card',
    pin: 'PIN',
    password: 'Password',
    qr: 'QR',
    iris: 'Iris',
    vein: 'Vein',
    multi: 'Multi-factor',
    manual: 'Manual',
    other: 'Other',
    not_reported: 'Not reported by device',
    unknown: 'Not reported by device',
  };
  return labels[key] || (value ? String(value) : '—');
}

function initialsFromName(name, code) {
  const text = isGeneratedEmployeeName(name, code)
    ? String(code || '')
    : String(name || '').trim();
  const bits = text.split(/\s+/).filter(Boolean);
  if (bits.length >= 2) return `${bits[0][0]}${bits[1][0]}`.toUpperCase();
  return text.slice(0, 2).toUpperCase() || '?';
}

function personFromPunch(row) {
  const code = String(row.device_employee_code || row.employee_code || row.device_user_id || '').trim();
  const nested = row.employee && typeof row.employee === 'object' ? row.employee : {};
  const name = String(
    nested.name || row.employee_name || row.employee_display_name || ''
  ).trim();
  return {
    name: displayPersonName(name, code),
    code,
    nameReported: Boolean(nested.name_reported || row.name_reported || (name && !isGeneratedEmployeeName(name, code))),
  };
}

function groupPunchesByPerson(rows) {
  const map = new Map();
  for (const row of rows) {
    const person = personFromPunch(row);
    const key = `${row.organisation_id || ''}::${person.code || row.id}`;
    if (!map.has(key)) {
      map.set(key, {
        name: person.name,
        code: person.code,
        matched: Boolean(row.employee_matched),
        device: row.device_name || row.terminal_code || '—',
        branch: row.branch || '—',
        punches: [],
      });
    }
    const group = map.get(key);
    group.punches.push(row);
    if (row.employee_name) group.name = displayPersonName(row.employee_name, person.code);
    if (row.employee_matched) group.matched = true;
    if (row.device_name) group.device = row.device_name;
    if (row.branch) group.branch = row.branch;
  }
  return [...map.values()].map((group) => {
    const punches = [...group.punches].sort((a, b) =>
      String(a.punch_time || '').localeCompare(String(b.punch_time || ''))
    );
    const ins = punches.filter((p) => String(p.event_type || '').toUpperCase() === 'IN');
    const outs = punches.filter((p) => String(p.event_type || '').toUpperCase() === 'OUT');
    const last = punches[punches.length - 1];
    const lastType = String(last?.event_type || '').toUpperCase();
    return {
      ...group,
      punches,
      firstIn: ins[0] || null,
      lastOut: outs[outs.length - 1] || null,
      last,
      present: lastType === 'IN' || lastType === 'BREAK_IN' || lastType === 'OT_IN',
    };
  }).sort((a, b) => a.name.localeCompare(b.name));
}

let attendanceView = 'people';

function applyAttendanceView() {
  const people = document.getElementById('attendance-people');
  const log = document.getElementById('attendance-log');
  if (people) people.classList.toggle('hidden', attendanceView !== 'people');
  if (log) log.classList.toggle('hidden', attendanceView !== 'log');
  document.querySelectorAll('[data-attendance-view]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.attendanceView === attendanceView);
  });
}

function renderAttendancePeople(groups) {
  const el = document.getElementById('attendance-people');
  if (!el) return;
  if (!groups.length) {
    el.innerHTML = `<div class="empty-state"><h3>No punches yet</h3><p>When employees punch on a biometric terminal, they appear here by name.</p></div>`;
    return;
  }
  el.innerHTML = groups.map((group) => {
    const chips = group.punches.slice(0, 12).map((p) => {
      const kind = String(p.event_type || '').toLowerCase();
      const cls = kind === 'in' ? 'in' : kind === 'out' ? 'out' : '';
      return `<span class="punch-chip ${cls}">${escapeHtml(String(p.event_type || '').toUpperCase())} ${escapeHtml(punchClock(p.punch_time, p.created_at))}</span>`;
    }).join('');
    const more = group.punches.length > 12 ? `<span class="punch-chip">+${group.punches.length - 12} more</span>` : '';
    return `
      <article class="person-card">
        <div class="person-head">
          <span class="person-avatar">${escapeHtml(initialsFromName(group.name, group.code))}</span>
          <div class="person-id">
            <h3>${escapeHtml(group.name)}</h3>
            <span class="person-code">Employee code ${escapeHtml(group.code || '—')} · ${escapeHtml(group.device)}</span>
          </div>
          ${statusPill(group.present ? 'IN' : (group.last?.event_type || 'UNKNOWN'))}
        </div>
        <div class="person-meta">
          <div>
            <div class="meta-label">First IN</div>
            <div class="meta-value">${group.firstIn ? escapeHtml(formatPunchTime(group.firstIn.punch_time, group.firstIn.created_at)) : '—'}</div>
          </div>
          <div>
            <div class="meta-label">Last OUT</div>
            <div class="meta-value">${group.lastOut ? escapeHtml(formatPunchTime(group.lastOut.punch_time, group.lastOut.created_at)) : '—'}</div>
          </div>
        </div>
        <div class="punch-chip-row">${chips}${more}</div>
      </article>`;
  }).join('');
}

let rosterSynced = false;

async function ensureEmployeeRoster() {
  if (rosterSynced) return;
  await api('/employees/sync-from-attendance', { method: 'POST' }).catch(() => ({}));
  rosterSynced = true;
}

async function loadAttendance() {
  const todayOnly = document.getElementById('attendance-today-only')?.checked !== false;
  const employee = String(document.getElementById('attendance-employee')?.value || '').trim();
  const terminal = String(document.getElementById('attendance-terminal')?.value || '').trim();
  const eventType = String(document.getElementById('attendance-event')?.value || '').trim().toUpperCase();

  await ensureEmployeeRoster();

  const params = new URLSearchParams({ order: 'desc', limit: '500' });
  if (todayOnly) {
    params.set('today', 'true');
  }
  if (employee) params.set('employee_code', employee);
  if (terminal) params.set('terminal_code', terminal);

  const [summary, punchesResp] = await Promise.all([
    api('/analytics/summary').catch(() => ({})),
    api(`/payroll/punches?${params.toString()}`).catch(() => ({ punches: [] })),
  ]);
  let punchRows = Array.isArray(punchesResp) ? punchesResp : (punchesResp.punches || []);
  if (eventType) {
    punchRows = punchRows.filter(r => String(r.event_type || '').toUpperCase() === eventType);
  }

  const people = groupPunchesByPerson(punchRows);
  const devices = new Set(punchRows.map(r => r.device_name || r.terminal_code).filter(Boolean));
  const inCount = punchRows.filter(r => String(r.event_type || '').toUpperCase() === 'IN').length;
  const outCount = punchRows.filter(r => String(r.event_type || '').toUpperCase() === 'OUT').length;
  const present = people.filter(p => p.present).length;

  renderStats(document.getElementById('attendance-stats'), [
    { label: todayOnly ? 'Punches today' : 'Punches loaded', value: punchRows.length },
    { label: 'People', value: people.length },
    { label: 'Currently in', value: present },
    { label: 'Devices', value: devices.size },
    { label: 'IN', value: inCount },
    { label: 'OUT', value: outCount },
    { label: 'Attendance today', value: summary.attendance_today ?? people.length },
  ]);

  renderAttendancePeople(people);
  renderTable(document.getElementById('table-attendance'), [
    { key: 'punch_time', label: 'Punch Time', cellClass: 'datetime', render: r => formatPunchTime(r.punch_time, r.created_at) },
    {
      key: 'employee_name',
      label: 'Person',
      render: r => {
        const person = personFromPunch(r);
        return `<strong>${escapeHtml(person.name)}</strong><div class="muted">Code ${escapeHtml(person.code || '—')}</div>`;
      },
    },
    { key: 'device_name', label: 'Device', render: r => escapeHtml(r.device_name || r.terminal_code || '—') },
    { key: 'event_type', label: 'Event', render: r => {
        const inferred = classificationLabel(r.classification_method);
        return `${statusPill(r.event_type)}${inferred ? `<div class="muted">${escapeHtml(inferred)}</div>` : ''}`;
      } },
    { key: 'branch', label: 'Branch', render: r => escapeHtml(r.branch || '—') },
    { key: 'verification_method', label: 'Method', render: r => escapeHtml(verificationLabel(r.verification_method)) },
    { key: 'created_at', label: 'Cloud received', cellClass: 'datetime', render: r => r.created_at ? formatDateTimeAligned(r.created_at) : '—' },
    { key: 'hrms_sync_status', label: 'Brixigo.com', render: r => statusPill(r.hrms_sync_status || 'pending') },
  ], punchRows);
  applyAttendanceView();
}

async function loadMonitoring() {
  const data = await api('/monitoring/dashboard');
  renderStats(document.getElementById('monitoring-stats'), [
    { label: 'Online', value: data.online },
    { label: 'Offline', value: data.offline },
    { label: 'Warning', value: data.warning },
    { label: 'Critical', value: data.critical },
    { label: 'Total Devices', value: data.total_devices },
    { label: 'Total Connectors', value: data.total_connectors },
  ]);
}

async function loadAnalytics() {
  const data = await api('/analytics/summary');
  renderStats(document.getElementById('analytics-stats'), [
    { label: 'Queue Alerts', value: data.queue_alerts },
    { label: 'Connectors Offline', value: data.connectors_offline },
    { label: 'Uptime %', value: data.uptime_percent },
    { label: 'Avg Health', value: data.avg_health_score },
  ]);
  makeChart('chart-uptime', 'bar', ['Uptime', 'Healthy', 'Warning', 'Critical'],
    [data.uptime_percent, data.devices_healthy, data.devices_warning, data.devices_critical], 'Metrics');
}

let connectorCache = {};

function hideConnectorDetails() {
  const panel = document.getElementById('connector-details');
  if (panel) {
    panel.classList.add('hidden');
    panel.innerHTML = '';
  }
}

function bindConnectorActions(root = document) {
  root.querySelectorAll('[data-connector-action]').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      const id = btn.dataset.id;
      const action = btn.dataset.connectorAction;
      try {
        if (action === 'details') await showConnectorDetails(id);
        else if (action === 'edit') await editConnector(id);
        else if (action === 'delete') await deleteConnector(id);
      } catch (err) {
        alert(err.message || 'Connector action failed');
      }
    });
  });
}

async function showConnectorDetails(connectorId) {
  const connector = await api(`/connectors/${connectorId}`);
  connectorCache[connectorId] = connector;
  const panel = document.getElementById('connector-details');
  if (!panel) return;
  const liveStatus = isConnectorOnline(connector) ? 'online' : (connector.status || 'offline');
  const fields = [
    ['Name', connector.name],
    ['Organisation', connector.organisation_name],
    ['Branch', connector.branch_name],
    ['Status', liveStatus],
    ['Version', connector.version],
    ['Hostname', connector.hostname],
    ['IP', connector.ip_address],
    ['OS', connector.operating_system],
    ['Devices', connector.connected_devices],
    ['Offline devices', connector.offline_devices],
    ['Queue', connector.queue_size],
    ['Active', connector.is_active],
    ['API key prefix', connector.api_key_prefix],
    ['Internet', connector.internet_status],
    ['Cloud', connector.cloud_status],
    ['Certificate', connector.certificate_status],
    ['CPU %', connector.cpu_percent],
    ['RAM %', connector.ram_percent],
    ['Disk %', connector.disk_percent],
    ['Last heartbeat', connector.last_heartbeat_at ? new Date(connector.last_heartbeat_at).toLocaleString() : ''],
    ['Connector ID', connector.id],
  ];
  panel.classList.remove('hidden');
  panel.innerHTML = `
    <div class="detail-actions" style="justify-content:space-between;margin-top:0;margin-bottom:0.75rem">
      <h3 style="margin:0">Connector details — ${formatDeviceField(connector.name)}</h3>
      <button type="button" class="btn-sm" id="btn-close-connector-details">Close</button>
    </div>
    <dl class="detail-grid">
      ${fields.map(([label, value]) => `
        <div>
          <dt>${label}</dt>
          <dd>${label === 'Status' ? statusPill(value) : formatDeviceField(value)}</dd>
        </div>
      `).join('')}
    </dl>
    <div class="detail-actions">
      <button type="button" class="btn btn-secondary btn-sm" data-connector-action="edit" data-id="${connector.id}">Edit Connector</button>
      <button type="button" class="btn-sm btn-danger" data-connector-action="delete" data-id="${connector.id}">Delete</button>
    </div>
  `;
  document.getElementById('btn-close-connector-details')?.addEventListener('click', hideConnectorDetails);
  bindConnectorActions(panel);
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function editConnector(connectorId) {
  const connector = connectorCache[connectorId] || await api(`/connectors/${connectorId}`);
  connectorCache[connectorId] = connector;
  const name = prompt('Connector name:', connector.name || '');
  if (name === null) return;
  const hostname = prompt('Hostname:', connector.hostname || '');
  if (hostname === null) return;
  const version = prompt('Version:', connector.version || '');
  if (version === null) return;
  const ip = prompt('IP address:', connector.ip_address || '');
  if (ip === null) return;
  const branch = prompt(
    'Branch name (blank to clear):',
    connector.branch_name && connector.branch_name !== '—' ? connector.branch_name : ''
  );
  if (branch === null) return;
  const activeRaw = prompt('Active? (yes/no):', connector.is_active ? 'yes' : 'no');
  if (activeRaw === null) return;

  const payload = {
    name: name.trim() || connector.name,
    hostname: hostname.trim(),
    version: version.trim(),
    ip_address: ip.trim(),
    branch_name: branch.trim(),
    is_active: ['yes', 'y', 'true', '1'].includes(String(activeRaw).trim().toLowerCase()),
  };

  await api(`/connectors/${connectorId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
  await loadConnectors();
  await showConnectorDetails(connectorId);
}

async function deleteConnector(connectorId) {
  const connector = connectorCache[connectorId] || await api(`/connectors/${connectorId}`);
  if (!confirm(`Delete connector "${connector.name}"?\nThis cannot be undone.`)) return;
  await api(`/connectors/${connectorId}`, { method: 'DELETE' });
  delete connectorCache[connectorId];
  hideConnectorDetails();
  await loadConnectors();
}

async function loadConnectors() {
  const rows = await api('/connectors');
  connectorCache = Object.fromEntries((rows || []).map(r => [r.id, r]));
  await refreshConnectorServerStatus(rows);
  renderTable(document.getElementById('table-connectors'), [
    { key: 'name', label: 'Connector Server' },
    { key: 'organisation_name', label: 'Organisation' },
    { key: 'branch_name', label: 'Branch' },
    { key: 'status', label: 'Status', render: r => statusPill(isConnectorOnline(r) ? 'online' : (r.status || 'offline')) },
    { key: 'version', label: 'Version' },
    { key: 'hostname', label: 'Host' },
    { key: 'connected_devices', label: 'Devices' },
    { key: 'queue_size', label: 'Queue' },
    { key: 'last_heartbeat_at', label: 'Last Heartbeat', render: r => r.last_heartbeat_at ? new Date(r.last_heartbeat_at).toLocaleString() : '—' },
    {
      key: 'actions',
      label: 'Actions',
      render: r => `
        <div class="row-actions">
          <button type="button" class="btn-sm" data-connector-action="details" data-id="${r.id}">Details</button>
          <button type="button" class="btn-sm" data-connector-action="edit" data-id="${r.id}">Edit</button>
          <button type="button" class="btn-sm btn-danger" data-connector-action="delete" data-id="${r.id}">Delete</button>
        </div>
      `,
    },
  ], rows || []);
  bindConnectorActions(document.getElementById('table-connectors'));
}

async function registerConnector() {
  const orgName = await defaultOrganisationName();
  const name = prompt('Connector name:', 'Branch Connector');
  if (!name) return;
  const org = prompt('Organisation name:', orgName);
  if (!org) return;
  const hostname = prompt('Hostname (optional):', window.location.hostname || 'local-pc') || '';
  const res = await api('/connectors/register', {
    method: 'POST',
    body: JSON.stringify({ organisation_name: org, name, hostname }),
  });
  const cred = document.getElementById('connector-credentials');
  cred.classList.remove('hidden');
  cred.innerHTML = `
    <strong>Copy these into on-prem Connector Settings:</strong><br/>
    Cloud URL: <b>${window.location.origin}</b><br/>
    Organisation ID: <b>${res.connector.organisation_name || org}</b><br/>
    Connector ID: <b>${res.connector.id}</b> (name: ${res.connector.name})<br/>
    API Key: <b>${res.api_key}</b><br/>
    Command Signing Secret: <b>change-me-brixigo-command-hmac-secret</b><br/>
    <em>Uncheck "Validate TLS" for local HTTP.</em>
  `;
  loadConnectors();
}

let deviceSearchQuery = '';
let deviceCache = {};

function hideDeviceDetails() {
  const panel = document.getElementById('device-details');
  if (panel) {
    panel.classList.add('hidden');
    panel.innerHTML = '';
  }
}

function formatDeviceField(value) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return String(value);
}

async function syncRelatedPages() {
  await refreshConnectorServerStatus().catch(() => {});
  if (currentModule === 'connectors') {
    await loadConnectors().catch(() => {});
  } else if (currentModule === 'dashboard') {
    await loadDashboard().catch(() => {});
  } else if (currentModule === 'monitoring') {
    await loadMonitoring().catch(() => {});
  }
}

function bindDeviceActions(root = document) {
  root.querySelectorAll('[data-device-action]').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      const id = btn.dataset.id;
      const action = btn.dataset.deviceAction;
      try {
        if (action === 'details') await showDeviceDetails(id);
        else if (action === 'edit') await editDevice(id);
        else if (action === 'delete') await deleteDevice(id);
      } catch (err) {
        alert(err.message || 'Device action failed');
      }
    });
  });
}

async function showDeviceDetails(deviceId) {
  const device = await api(`/devices/${deviceId}`);
  deviceCache[deviceId] = device;
  const panel = document.getElementById('device-details');
  if (!panel) return;
  const fields = [
    ['Name', device.name],
    ['Organisation', device.organisation_name],
    ['Branch', device.branch_name],
    ['Connector', device.connector_name],
    ['Terminal', device.terminal_code],
    ['Status', device.status],
    ['Health', device.health_status],
    ['Score', device.health_score],
    ['IP', device.ip_address],
    ['Port', device.port],
    ['Firmware', device.firmware],
    ['Manufacturer', device.manufacturer],
    ['Model', device.model],
    ['Serial', device.serial_number],
    ['Enabled', device.enabled],
    ['Last Sync', device.last_sync_at ? new Date(device.last_sync_at).toLocaleString() : ''],
    ['Last Attendance', device.last_attendance_at ? new Date(device.last_attendance_at).toLocaleString() : ''],
    ['Device ID', device.id],
  ];
  panel.classList.remove('hidden');
  panel.innerHTML = `
    <div class="detail-actions" style="justify-content:space-between;margin-top:0;margin-bottom:0.75rem">
      <h3 style="margin:0">Device details — ${formatDeviceField(device.name)}</h3>
      <button type="button" class="btn-sm" id="btn-close-device-details">Close</button>
    </div>
    <dl class="detail-grid">
      ${fields.map(([label, value]) => `
        <div>
          <dt>${label}</dt>
          <dd>${label === 'Status' || label === 'Health' ? statusPill(value) : formatDeviceField(value)}</dd>
        </div>
      `).join('')}
    </dl>
    <div class="detail-actions">
      <button type="button" class="btn btn-secondary btn-sm" data-device-action="edit" data-id="${device.id}">Edit Device</button>
      <button type="button" class="btn-sm btn-danger" data-device-action="delete" data-id="${device.id}">Delete Device</button>
    </div>
  `;
  document.getElementById('btn-close-device-details')?.addEventListener('click', hideDeviceDetails);
  bindDeviceActions(panel);
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

let employeeModalState = { mode: 'create', id: '', code: '', name: '' };

function closeModal() {
  document.getElementById('modal-overlay')?.classList.add('hidden');
}

function openEmployeeModal(existing = null) {
  employeeModalState = existing?.id
    ? { mode: 'edit', id: existing.id, code: existing.code || '', name: existing.name || '' }
    : { mode: 'create', id: '', code: '', name: '' };
  const title = document.getElementById('modal-title');
  const fields = document.getElementById('modal-fields');
  const err = document.getElementById('modal-error');
  if (title) title.textContent = employeeModalState.mode === 'edit' ? 'Set employee name' : 'Add employee';
  if (err) {
    err.textContent = '';
    err.classList.add('hidden');
  }
  if (fields) {
    fields.innerHTML = `
      <label for="modal-emp-code">Employee code</label>
      <input id="modal-emp-code" name="code" value="${escapeHtml(employeeModalState.code)}" ${employeeModalState.mode === 'edit' ? 'readonly' : 'required'} placeholder="Device user ID, e.g. 5" />
      <label for="modal-emp-name">Full name</label>
      <input id="modal-emp-name" name="name" value="${escapeHtml(/^Employee\s+/i.test(employeeModalState.name) ? '' : employeeModalState.name)}" required placeholder="e.g. Anita Sharma" />
    `;
  }
  document.getElementById('modal-overlay')?.classList.remove('hidden');
  document.getElementById('modal-emp-name')?.focus();
}

async function submitEmployeeModal(e) {
  e.preventDefault();
  const err = document.getElementById('modal-error');
  const code = String(document.getElementById('modal-emp-code')?.value || '').trim();
  const name = String(document.getElementById('modal-emp-name')?.value || '').trim();
  if (!code || !name) return;
  try {
    if (employeeModalState.mode === 'edit' && employeeModalState.id) {
      await api(`/employees/${employeeModalState.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ name }),
      });
    } else {
      const orgName = await defaultOrganisationName();
      await api('/employees', {
        method: 'POST',
        body: JSON.stringify({ organisation_name: orgName, employee_code: code, name }),
      });
    }
    closeModal();
    loadEmployees(document.getElementById('employee-search')?.value || '');
  } catch (ex) {
    if (err) {
      err.textContent = ex.message || 'Could not save employee';
      err.classList.remove('hidden');
    }
  }
}

function suggestTerminalCode(name = '') {
  const base = String(name || 'DEVICE')
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 16) || 'DEVICE';
  return `${base}-${String(Date.now()).slice(-4)}`;
}

function deviceFormValues(form) {
  const data = new FormData(form);
  const portRaw = String(data.get('port') || '').trim();
  return {
    name: String(data.get('name') || '').trim(),
    terminal_code: String(data.get('terminal_code') || '').trim(),
    ip_address: String(data.get('ip_address') || '').trim(),
    port: portRaw ? Number(portRaw) : 4370,
    firmware: String(data.get('firmware') || '').trim(),
    manufacturer: String(data.get('manufacturer') || '').trim(),
    model: String(data.get('model') || '').trim(),
    serial_number: String(data.get('serial_number') || '').trim(),
    branch_name: String(data.get('branch_name') || '').trim(),
    connector_name: String(data.get('connector_name') || '').trim(),
    organisation_name: String(data.get('organisation_name') || '').trim(),
    enabled: data.get('enabled') === 'on',
  };
}

async function openDeviceForm(mode, deviceId = null) {
  const panel = document.getElementById('device-details');
  if (!panel) return;

  const [orgName, connectors, device] = await Promise.all([
    defaultOrganisationName(),
    api('/connectors').catch(() => []),
    deviceId ? api(`/devices/${deviceId}`) : Promise.resolve(null),
  ]);
  if (device) deviceCache[deviceId] = device;

  const defaultConnector = connectors.find(c => isConnectorOnline(c)) || connectors[0];
  const selectedConnector = device?.connector_name && device.connector_name !== '—'
    ? device.connector_name
    : (defaultConnector?.name || '');
  const terminalDefault = device?.terminal_code || suggestTerminalCode(device?.name || 'Device');
  const title = mode === 'add' ? 'Add device' : `Edit device — ${escapeHtml(device?.name || '')}`;

  const connectorOptions = [
    '<option value="">— None —</option>',
    ...connectors.map(c => {
      const selected = c.name === selectedConnector ? ' selected' : '';
      return `<option value="${escapeHtml(c.name)}"${selected}>${escapeHtml(c.name)}</option>`;
    }),
  ].join('');

  panel.classList.remove('hidden');
  panel.innerHTML = `
    <div class="detail-actions" style="justify-content:space-between;margin-top:0;margin-bottom:0.75rem">
      <h3 style="margin:0">${title}</h3>
      <button type="button" class="btn-sm" id="btn-close-device-details">Close</button>
    </div>
    <form id="device-edit-form" class="device-form" autocomplete="off">
      <div class="form-field">
        <label for="device-name">Device name</label>
        <input id="device-name" name="name" required value="${escapeHtml(device?.name || 'Device')}" />
      </div>
      <div class="form-field">
        <label for="device-terminal">Terminal code</label>
        <input id="device-terminal" name="terminal_code" required placeholder="e.g. GATE-01" value="${escapeHtml(terminalDefault)}" />
      </div>
      <div class="form-field">
        <label for="device-ip">IP address</label>
        <input id="device-ip" name="ip_address" placeholder="192.168.1.50" value="${escapeHtml(device?.ip_address || '')}" />
      </div>
      <div class="form-field">
        <label for="device-port">Port</label>
        <input id="device-port" name="port" type="number" min="1" max="65535" value="${escapeHtml(device?.port ?? 4370)}" />
      </div>
      <div class="form-field">
        <label for="device-firmware">Firmware</label>
        <input id="device-firmware" name="firmware" placeholder="e.g. 6.60" value="${escapeHtml(device?.firmware || '')}" />
      </div>
      <div class="form-field">
        <label for="device-manufacturer">Manufacturer</label>
        <input id="device-manufacturer" name="manufacturer" placeholder="e.g. ZKTeco" value="${escapeHtml(device?.manufacturer || '')}" />
      </div>
      <div class="form-field">
        <label for="device-model">Model</label>
        <input id="device-model" name="model" placeholder="e.g. K40" value="${escapeHtml(device?.model || '')}" />
      </div>
      <div class="form-field">
        <label for="device-serial">Serial number</label>
        <input id="device-serial" name="serial_number" value="${escapeHtml(device?.serial_number || '')}" />
      </div>
      <div class="form-field">
        <label for="device-connector">Connector</label>
        <select id="device-connector" name="connector_name">${connectorOptions}</select>
      </div>
      <div class="form-field">
        <label for="device-branch">Branch</label>
        <input id="device-branch" name="branch_name" placeholder="Optional branch name" value="${escapeHtml(device?.branch_name && device.branch_name !== '—' ? device.branch_name : '')}" />
      </div>
      ${mode === 'add' ? `
      <div class="form-field">
        <label for="device-org">Organisation</label>
        <input id="device-org" name="organisation_name" required value="${escapeHtml(orgName)}" />
      </div>` : ''}
      <div class="form-field form-check">
        <input id="device-enabled" name="enabled" type="checkbox" ${(device?.enabled ?? true) ? 'checked' : ''} />
        <label for="device-enabled">Enabled</label>
      </div>
      <div class="form-field full">
        <p class="hint" style="margin:0">Fill terminal code plus network and hardware details so Devices, Connector Server, and Dashboard stay aligned.</p>
        <p id="device-form-error" class="form-error hidden"></p>
      </div>
    </form>
    <div class="detail-actions">
      <button type="submit" form="device-edit-form" class="btn btn-primary" id="btn-save-device">${mode === 'add' ? 'Add Device' : 'Save Changes'}</button>
      <button type="button" class="btn btn-secondary" id="btn-cancel-device">Cancel</button>
    </div>
  `;

  document.getElementById('btn-close-device-details')?.addEventListener('click', hideDeviceDetails);
  document.getElementById('btn-cancel-device')?.addEventListener('click', () => {
    if (deviceId) showDeviceDetails(deviceId).catch(console.error);
    else hideDeviceDetails();
  });

  const form = document.getElementById('device-edit-form');
  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const errEl = document.getElementById('device-form-error');
    const saveBtn = document.getElementById('btn-save-device');
    const values = deviceFormValues(form);
    if (!values.name) {
      errEl.textContent = 'Device name is required.';
      errEl.classList.remove('hidden');
      return;
    }
    if (!values.terminal_code) {
      errEl.textContent = 'Terminal code is required.';
      errEl.classList.remove('hidden');
      return;
    }
    errEl.classList.add('hidden');
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = 'Saving…';
    }
    try {
      if (mode === 'add') {
        const created = await api('/devices', {
          method: 'POST',
          body: JSON.stringify({
            organisation_name: values.organisation_name || orgName,
            name: values.name,
            terminal_code: values.terminal_code,
            ip_address: values.ip_address,
            port: values.port,
            firmware: values.firmware,
            manufacturer: values.manufacturer,
            model: values.model,
            serial_number: values.serial_number,
            branch_name: values.branch_name || undefined,
            connector_name: values.connector_name || undefined,
            enabled: values.enabled,
          }),
        });
        await loadDevices(deviceSearchQuery);
        await syncRelatedPages();
        await showDeviceDetails(created.id);
      } else {
        const connectorMatch = connectors.find(c => c.name === values.connector_name);
        await api(`/devices/${deviceId}`, {
          method: 'PATCH',
          body: JSON.stringify({
            name: values.name,
            terminal_code: values.terminal_code,
            ip_address: values.ip_address,
            port: values.port,
            firmware: values.firmware,
            manufacturer: values.manufacturer,
            model: values.model,
            serial_number: values.serial_number,
            branch_name: values.branch_name,
            connector_id: connectorMatch ? connectorMatch.id : null,
            enabled: values.enabled,
          }),
        });
        await loadDevices(deviceSearchQuery);
        await syncRelatedPages();
        await showDeviceDetails(deviceId);
      }
    } catch (err) {
      errEl.textContent = err.message || 'Save failed';
      errEl.classList.remove('hidden');
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.textContent = mode === 'add' ? 'Add Device' : 'Save Changes';
      }
    }
  });

  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  document.getElementById('device-terminal')?.focus();
}

async function addDevice() {
  await openDeviceForm('add');
}

async function editDevice(deviceId) {
  await openDeviceForm('edit', deviceId);
}

async function deleteDevice(deviceId) {
  const device = deviceCache[deviceId] || await api(`/devices/${deviceId}`);
  if (!confirm(`Delete device "${device.name}"?\nThis cannot be undone.`)) return;
  await api(`/devices/${deviceId}`, { method: 'DELETE' });
  delete deviceCache[deviceId];
  hideDeviceDetails();
  await loadDevices(deviceSearchQuery);
  await syncRelatedPages();
}

async function loadDevices(search = deviceSearchQuery) {
  deviceSearchQuery = search || '';
  const q = deviceSearchQuery ? `?search=${encodeURIComponent(deviceSearchQuery)}` : '';
  const rows = await api(`/devices${q}`);
  deviceCache = Object.fromEntries((rows || []).map(r => [r.id, r]));
  renderTable(document.getElementById('table-devices'), [
    { key: 'name', label: 'Device' },
    { key: 'organisation_name', label: 'Organisation' },
    { key: 'branch_name', label: 'Branch' },
    { key: 'connector_name', label: 'Connector' },
    { key: 'terminal_code', label: 'Terminal' },
    { key: 'status', label: 'Status', render: r => statusPill(r.status) },
    { key: 'health_status', label: 'Health', render: r => statusPill(r.health_status) },
    { key: 'health_score', label: 'Score' },
    { key: 'ip_address', label: 'IP' },
    { key: 'firmware', label: 'Firmware' },
    { key: 'enabled', label: 'Enabled', render: r => r.enabled ? 'Yes' : 'No' },
    {
      key: 'actions',
      label: 'Actions',
      render: r => `
        <div class="row-actions">
          <button type="button" class="btn-sm" data-device-action="details" data-id="${r.id}">Details</button>
          <button type="button" class="btn-sm" data-device-action="edit" data-id="${r.id}">Edit</button>
          <button type="button" class="btn-sm btn-danger" data-device-action="delete" data-id="${r.id}">Delete</button>
        </div>
      `,
    },
  ], rows || []);
  bindDeviceActions(document.getElementById('table-devices'));
}

async function loadCommands(status = '') {
  const q = status ? `?status=${status}` : '';
  const rows = await api(`/commands${q}`);
  renderTable(document.getElementById('table-commands'), [
    { key: 'command_type', label: 'Type' },
    { key: 'status', label: 'Status', render: r => statusPill(r.status) },
    { key: 'organisation_name', label: 'Organisation' },
    { key: 'connector_name', label: 'Connector' },
    { key: 'device_name', label: 'Device' },
    { key: 'issued_at', label: 'Issued', render: r => new Date(r.issued_at).toLocaleString() },
    { key: 'result_message', label: 'Result' },
  ], rows);
}

async function loadEmployees(search = '') {
  const q = search ? `?search=${encodeURIComponent(search)}` : '';
  const rows = await api(`/employees${q}`);
  const statsEl = document.getElementById('employee-stats');
  const withPunches = rows.filter(r => Number(r.punch_count || 0) > 0).length;
  if (statsEl) {
    renderStats(statsEl, [
      { label: 'Employees', value: rows.length },
      { label: 'With punches', value: withPunches },
      { label: 'Need a name', value: rows.filter(r => !r.name_reported).length },
    ]);
  }
  const board = document.getElementById('employee-board');
  if (!board) return;
  if (!rows.length) {
    board.innerHTML = `<div class="empty-state"><h3>No employees yet</h3><p>Punch on a biometric terminal, then refresh. People are added automatically from those punches.</p></div>`;
    return;
  }
  board.innerHTML = rows.map((r) => `
    <article class="person-card">
      <div class="person-head">
        <span class="person-avatar">${escapeHtml(initialsFromName(r.name, r.employee_code))}</span>
        <div class="person-id">
          <h3>${escapeHtml(displayPersonName(r.name, r.employee_code))}</h3>
          <span class="person-code">Employee code ${escapeHtml(r.employee_code || r.device_user_id || '—')} · ${escapeHtml(r.organisation_name || '')}${r.branch_name && r.branch_name !== '—' ? ` · ${escapeHtml(r.branch_name)}` : ''}</span>
        </div>
        ${statusPill(r.status || 'active')}
      </div>
      <div class="person-meta">
        <div>
          <div class="meta-label">Punches</div>
          <div class="meta-value">${Number(r.punch_count || 0)}</div>
        </div>
        <div>
          <div class="meta-label">Last punch</div>
          <div class="meta-value">${r.last_punch_at ? escapeHtml(formatPunchTime(r.last_punch_at)) : '—'}</div>
        </div>
      </div>
      <div class="person-actions">
        <button type="button" class="btn-sm" data-emp-rename="${escapeHtml(r.id)}" data-emp-name="${escapeHtml(r.name || '')}" data-emp-code="${escapeHtml(r.employee_code || '')}">Set name</button>
      </div>
    </article>
  `).join('');
  board.querySelectorAll('[data-emp-rename]').forEach((btn) => {
    btn.addEventListener('click', () => openEmployeeModal({
      id: btn.dataset.empRename,
      name: btn.dataset.empName,
      code: btn.dataset.empCode,
    }));
  });
}

async function loadTemplates() {
  const rows = await api('/templates');
  renderTable(document.getElementById('table-templates'), [
    { key: 'employee_code', label: 'Employee' },
    { key: 'template_type', label: 'Type' },
    { key: 'finger_index', label: 'Finger' },
    { key: 'checksum', label: 'Checksum', render: r => (r.checksum || '').slice(0, 12) + '…' },
    { key: 'version', label: 'Version' },
    { key: 'vendor', label: 'Vendor' },
  ], rows);
}

async function loadProvisioning() {
  const [discover, unassigned] = await Promise.all([
    api('/provisioning/discover'),
    api('/provisioning/unassigned-devices'),
  ]);
  document.getElementById('provisioning-wizard').innerHTML = `
    <div class="wizard-step"><h3>Step 1: Discover</h3><p>${discover.message}</p>
      <ul>${discover.items.map(i => `<li>${i.name} (${i.hostname})</li>`).join('') || '<li>None pending</li>'}</ul></div>
    <div class="wizard-step"><h3>Step 2: Assign Locations</h3><p>${unassigned.message}</p>
      <ul>${unassigned.items.map(i => `<li>${i.name} — ${i.terminal_code || 'no terminal'}</li>`).join('') || '<li>All assigned</li>'}</ul></div>`;
}

async function loadFirmware() {
  const rows = await api('/firmware/packages');
  renderTable(document.getElementById('table-firmware'), [
    { key: 'manufacturer', label: 'Manufacturer' },
    { key: 'model', label: 'Model' },
    { key: 'version', label: 'Version' },
    { key: 'size_bytes', label: 'Size', render: r => `${Math.round(r.size_bytes / 1024)} KB` },
    { key: 'is_latest', label: 'Latest', render: r => r.is_latest ? 'Yes' : 'No' },
  ], rows);
}

async function loadArtifacts() {
  const rows = await api('/support/artifacts');
  renderTable(document.getElementById('table-artifacts'), [
    { key: 'filename', label: 'File' },
    { key: 'artifact_type', label: 'Type' },
    { key: 'connector_id', label: 'Connector', render: r => r.meta_json?.connector_name || r.connector_id || '—' },
    { key: 'size_bytes', label: 'Size' },
    { key: 'created_at', label: 'Created', render: r => new Date(r.created_at).toLocaleString() },
  ], rows);
}

async function loadReports() {
  const rows = await api('/reports');
  renderTable(document.getElementById('table-reports'), [
    { key: 'report_type', label: 'Type' },
    { key: 'format', label: 'Format' },
    { key: 'status', label: 'Status', render: r => statusPill(r.status) },
    { key: 'created_at', label: 'Created', render: r => new Date(r.created_at).toLocaleString() },
    { key: 'id', label: 'Download', render: r => r.status === 'completed' ? `<a href="${API}/reports/${r.id}/download" target="_blank">Download</a>` : '—' },
  ], rows);
}

async function loadNotifications() {
  const rows = await api('/notifications');
  renderTable(document.getElementById('table-notifications'), [
    { key: 'severity', label: 'Severity', render: r => statusPill(r.severity) },
    { key: 'title', label: 'Title' },
    { key: 'message', label: 'Message' },
    { key: 'event_type', label: 'Event' },
    { key: 'acknowledged', label: 'Ack', render: r => r.acknowledged ? 'Yes' : `<button class="btn btn-secondary btn-ack" data-id="${r.id}">Ack</button>` },
    { key: 'created_at', label: 'Time', render: r => new Date(r.created_at).toLocaleString() },
  ], rows);
  document.querySelectorAll('.btn-ack').forEach(btn => {
    btn.addEventListener('click', async () => {
      await api(`/notifications/${btn.dataset.id}/ack`, { method: 'POST' });
      loadNotifications();
    });
  });
}

async function loadAudit(search = '') {
  const q = search ? `?search=${encodeURIComponent(search)}` : '';
  const rows = await api(`/audit${q}`);
  renderTable(document.getElementById('table-audit'), [
    { key: 'created_at', label: 'Time', render: r => new Date(r.created_at).toLocaleString() },
    { key: 'actor_email', label: 'Actor' },
    { key: 'action', label: 'Action' },
    { key: 'resource_type', label: 'Resource' },
    { key: 'success', label: 'OK', render: r => r.success ? '✓' : '✗' },
    { key: 'message', label: 'Message' },
  ], rows);
}

async function loadPolicies() {
  const rows = await api('/settings/policies');
  renderTable(document.getElementById('table-policies'), [
    { key: 'policy_key', label: 'Key' },
    { key: 'organisation_id', label: 'Organisation', render: r => r.organisation_name || (r.organisation_id ? 'Global' : 'Platform') },
    { key: 'policy_json', label: 'Value', render: r => JSON.stringify(r.policy_json).slice(0, 80) },
  ], rows);
}

async function loadWebhooks() {
  const rows = await api('/webhooks');
  renderTable(document.getElementById('table-webhooks'), [
    { key: 'url', label: 'URL' },
    { key: 'event_types_json', label: 'Events', render: r => (r.event_types_json || []).join(', ') },
    { key: 'is_active', label: 'Active', render: r => r.is_active ? 'Yes' : 'No' },
  ], rows);
}

async function loadOrganisations() {
  const rows = await api('/tenancy/organisations');
  renderTable(document.getElementById('table-organisations'), [
    { key: 'name', label: 'Name' },
    { key: 'code', label: 'Code' },
    { key: 'status', label: 'Status', render: r => statusPill(r.status) },
    { key: 'timezone', label: 'Timezone' },
  ], rows);
}

async function loadBranches() {
  const rows = await api('/tenancy/branches');
  const orgs = await api('/tenancy/organisations');
  const orgMap = Object.fromEntries(orgs.map(o => [o.id, o.name]));
  renderTable(document.getElementById('table-branches'), [
    { key: 'name', label: 'Branch' },
    { key: 'organisation_id', label: 'Organisation', render: r => orgMap[r.organisation_id] || '—' },
    { key: 'code', label: 'Code' },
    { key: 'address', label: 'Address' },
    { key: 'status', label: 'Status', render: r => statusPill(r.status) },
  ], rows);
}

async function loadDepartments() {
  const rows = await api('/tenancy/departments');
  const branches = await api('/tenancy/branches');
  const branchMap = Object.fromEntries(branches.map(b => [b.id, b.name]));
  renderTable(document.getElementById('table-departments'), [
    { key: 'name', label: 'Department' },
    { key: 'branch_id', label: 'Branch', render: r => branchMap[r.branch_id] || '—' },
  ], rows);
}

async function loadLocations() {
  const [buildings, floors, gates, branches] = await Promise.all([
    api('/tenancy/buildings'),
    api('/tenancy/floors'),
    api('/tenancy/gates'),
    api('/tenancy/branches'),
  ]);
  const branchMap = Object.fromEntries(branches.map(b => [b.id, b.name]));
  const buildingMap = Object.fromEntries(buildings.map(b => [b.id, b.name]));
  const floorMap = Object.fromEntries(floors.map(f => [f.id, f.name]));
  const rows = [
    ...buildings.map(b => ({ type: 'Building', name: b.name, parent: branchMap[b.branch_id] || '—' })),
    ...floors.map(f => ({ type: 'Floor', name: f.name, parent: buildingMap[f.building_id] || '—' })),
    ...gates.map(g => ({ type: 'Gate', name: g.name, parent: floorMap[g.floor_id] || '—' })),
  ];
  renderTable(document.getElementById('table-locations'), [
    { key: 'type', label: 'Type' },
    { key: 'name', label: 'Name' },
    { key: 'parent', label: 'Parent' },
  ], rows);
}

const loaders = {
  dashboard: loadDashboard,
  monitoring: loadMonitoring,
  analytics: loadAnalytics,
  attendance: loadAttendance,
  connectors: loadConnectors,
  devices: loadDevices,
  commands: loadCommands,
  employees: loadEmployees,
  templates: loadTemplates,
  provisioning: loadProvisioning,
  firmware: loadFirmware,
  support: loadArtifacts,
  reports: loadReports,
  notifications: loadNotifications,
  audit: loadAudit,
  settings: loadPolicies,
  webhooks: loadWebhooks,
  organisations: loadOrganisations,
  branches: loadBranches,
  departments: loadDepartments,
  locations: loadLocations,
};

const titles = {
  dashboard: 'Dashboard', monitoring: 'Monitoring', analytics: 'Analytics',
  attendance: 'Attendance',
  connectors: 'Connector Server', devices: 'Devices', commands: 'Command Center',
  employees: 'Employees', templates: 'Biometric Templates', provisioning: 'Provisioning Wizard',
  firmware: 'Firmware', support: 'Support & Diagnostics', reports: 'Reports',
  notifications: 'Notifications', audit: 'Audit Logs', settings: 'System Policies',
  webhooks: 'Webhooks', organisations: 'Organisations', branches: 'Branches',
  departments: 'Departments', locations: 'Buildings / Floors / Gates',
};

function navigate(module) {
  currentModule = module;
  if (module !== 'devices') hideDeviceDetails();
  if (module !== 'connectors') hideConnectorDetails();
  document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.module === module));
  document.querySelectorAll('.module-panel').forEach(p => p.classList.add('hidden'));
  const panel = document.getElementById(`module-${module}`);
  if (panel) panel.classList.remove('hidden');
  document.getElementById('module-title').textContent = titles[module] || module;
  const loader = loaders[module];
  if (loader) loader().catch(err => console.error(module, err));
  startLiveRefresh(module);
}

function applyAuthSession(data) {
  setAuth(data.access_token, {
    email: data.email,
    full_name: data.full_name,
    role: data.role,
    organisation_id: data.organisation_id,
    organisation_name: data.organisation_name,
  });
  showApp();
  navigate('dashboard');
}

function formatApiError(detail, fallback) {
  if (!detail) return fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
  }
  return fallback;
}

function showAuthView(mode) {
  const loginView = document.getElementById('auth-login-view');
  const registerView = document.getElementById('auth-register-view');
  if (!loginView || !registerView) return;
  const isRegister = mode === 'register';
  loginView.classList.toggle('hidden', isRegister);
  registerView.classList.toggle('hidden', !isRegister);
  document.getElementById('login-error')?.classList.add('hidden');
  document.getElementById('register-error')?.classList.add('hidden');
}

document.getElementById('show-register')?.addEventListener('click', () => showAuthView('register'));
document.getElementById('show-login')?.addEventListener('click', () => showAuthView('login'));

document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errEl = document.getElementById('login-error');
  const submit = document.getElementById('login-submit');
  errEl.classList.add('hidden');
  const previous = submit ? submit.textContent : '';
  if (submit) {
    submit.disabled = true;
    submit.textContent = 'Signing in…';
  }
  try {
    const email = document.getElementById('login-email').value;
    const password = document.getElementById('login-password').value;
    const data = await fetch(`${API}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    }).then(r => r.json());
    if (data.detail) throw new Error(formatApiError(data.detail, 'Login failed'));
    applyAuthSession(data);
  } catch (err) {
    errEl.textContent = err.message || 'Login failed';
    errEl.classList.remove('hidden');
  } finally {
    if (submit) {
      submit.disabled = false;
      submit.textContent = previous || 'Sign in';
    }
  }
});

document.getElementById('register-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const errEl = document.getElementById('register-error');
  const submit = document.getElementById('register-submit');
  errEl.classList.add('hidden');
  const previous = submit ? submit.textContent : '';
  if (submit) {
    submit.disabled = true;
    submit.textContent = 'Creating…';
  }
  try {
    const payload = {
      organisation_name: document.getElementById('register-org').value.trim(),
      full_name: document.getElementById('register-name').value.trim(),
      email: document.getElementById('register-email').value.trim(),
      password: document.getElementById('register-password').value,
    };
    const data = await fetch(`${API}/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(r => r.json());
    if (data.detail) throw new Error(formatApiError(data.detail, 'Registration failed'));
    applyAuthSession(data);
  } catch (err) {
    errEl.textContent = err.message || 'Registration failed';
    errEl.classList.remove('hidden');
  } finally {
    if (submit) {
      submit.disabled = false;
      submit.textContent = previous || 'Create account';
    }
  }
});

document.getElementById('logout-btn').addEventListener('click', () => { clearAuth(); showLogin(); });
document.getElementById('sidebar-nav').addEventListener('click', (e) => {
  const btn = e.target.closest('.nav-item');
  if (btn) navigate(btn.dataset.module);
});

document.getElementById('btn-refresh-connectors')?.addEventListener('click', loadConnectors);
document.getElementById('btn-register-connector')?.addEventListener('click', () => registerConnector().catch(console.error));
document.getElementById('btn-refresh-attendance')?.addEventListener('click', () => loadAttendance().catch(console.error));
document.getElementById('attendance-today-only')?.addEventListener('change', () => loadAttendance().catch(console.error));
document.getElementById('attendance-event')?.addEventListener('change', () => loadAttendance().catch(console.error));
let attendanceFilterTimer = null;
function scheduleAttendanceReload() {
  clearTimeout(attendanceFilterTimer);
  attendanceFilterTimer = setTimeout(() => loadAttendance().catch(console.error), 300);
}
document.getElementById('attendance-employee')?.addEventListener('input', scheduleAttendanceReload);
document.getElementById('attendance-terminal')?.addEventListener('input', scheduleAttendanceReload);
document.querySelectorAll('[data-goto]').forEach(el => {
  el.addEventListener('click', () => navigate(el.dataset.goto));
});
document.getElementById('device-search')?.addEventListener('input', (e) => loadDevices(e.target.value));
document.getElementById('btn-add-device')?.addEventListener('click', () => addDevice().catch(err => alert(err.message || 'Add device failed')));
document.getElementById('btn-refresh-devices')?.addEventListener('click', () => loadDevices(deviceSearchQuery).catch(console.error));
document.getElementById('employee-search')?.addEventListener('input', (e) => loadEmployees(e.target.value));
document.getElementById('audit-search')?.addEventListener('input', (e) => loadAudit(e.target.value));
document.getElementById('command-status-filter')?.addEventListener('change', (e) => loadCommands(e.target.value));

// Keep sidebar Connector Server status live even when not on that page
setInterval(() => {
  if (!getToken() || document.getElementById('app-shell')?.classList.contains('hidden')) return;
  refreshConnectorServerStatus().catch(() => {});
}, LIVE_REFRESH_MS);

document.getElementById('btn-add-employee')?.addEventListener('click', () => openEmployeeModal());
document.getElementById('btn-sync-employees')?.addEventListener('click', async () => {
  rosterSynced = false;
  await ensureEmployeeRoster();
  loadEmployees(document.getElementById('employee-search')?.value || '');
});
document.querySelectorAll('[data-attendance-view]').forEach((btn) => {
  btn.addEventListener('click', () => {
    attendanceView = btn.dataset.attendanceView || 'people';
    applyAttendanceView();
  });
});
document.getElementById('modal-cancel')?.addEventListener('click', closeModal);
document.getElementById('modal-overlay')?.addEventListener('click', (e) => {
  if (e.target.id === 'modal-overlay') closeModal();
});
document.getElementById('modal-form')?.addEventListener('submit', submitEmployeeModal);

document.getElementById('btn-create-report')?.addEventListener('click', async () => {
  const orgName = await defaultOrganisationName();
  await api('/reports', {
    method: 'POST',
    body: JSON.stringify({
      report_type: document.getElementById('report-type').value,
      format: document.getElementById('report-format').value,
      organisation_name: orgName,
    }),
  });
  loadReports();
});

document.getElementById('device-export')?.addEventListener('click', (e) => {
  const token = getToken();
  if (token) e.target.href = `${API}/devices/export/csv`;
});

if (getToken()) {
  showApp();
  navigate('dashboard');
} else {
  showLogin();
}
