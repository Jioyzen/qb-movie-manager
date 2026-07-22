/* ─── QB 影视管理工具 - 前端 SPA ─────────────────────────────── */

// ─── 状态 ─────────────────────────────────────────────────────
const state = {
  step: 0,           // 0=配置 1=获取种子 2=深度分析 3=去重筛选 4=清理删除
  config: null,
  torrents: [],
  profiles: [],
  dedupResults: [],
  dedupOverview: null,
  busy: false,
  pollTimer: null,
  // 去重用户手动切换
  keepOverrides: {},  // { "torrent_hash|file_index": true/false }
};

// ─── 工具函数 ────────────────────────────────────────────────
const api = async (url, opts = {}) => {
  try {
    const r = await fetch(url, {
      headers: { 'Content-Type': 'application/json', ...opts.headers },
      ...opts,
    });
    return await r.json();
  } catch (e) {
    showToast(e.message, 'error');
    return null;
  }
};

const showToast = (msg, type = 'info') => {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast ${type}`;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.add('hidden'), 3000);
};

const formatSize = (bytes) => {
  if (!bytes) return '?';
  const u = ['B','KB','MB','GB','TB'];
  let i = 0;
  let s = bytes;
  while (s >= 1024 && i < u.length - 1) { s /= 1024; i++; }
  return `${s.toFixed(1)} ${u[i]}`;
};

const h = (tag, attrs = {}, ...children) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'className') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'innerHTML') el.innerHTML = v;
    else el.setAttribute(k, v);
  }
  for (const c of children) {
    if (typeof c === 'string') el.appendChild(document.createTextNode(c));
    else if (c) el.appendChild(c);
  }
  return el;
};

// ─── 状态栏更新 ───────────────────────────────────────────────
const setStatus = (text, busy = false) => {
  document.getElementById('statusText').textContent = text;
  const dot = document.getElementById('statusDot');
  dot.className = 'status-dot' + (busy ? ' busy' : '');
};

// ─── 轮询进度 ─────────────────────────────────────────────────
const startPolling = () => {
  if (state.pollTimer) return;
  state.pollTimer = setInterval(async () => {
    const data = await api('/api/progress');
    if (!data) return;

    if (data.running) {
      state.busy = true;
      setStatus(data.progress.message, true);
      // 实时更新进度条（如果存在）
      updateProgressUI(data.progress);
    } else if (state.busy || data.progress.current > 0) {
      // 任务刚完成，或有缓存数据
      state.busy = false;
      clearInterval(state.pollTimer);
      state.pollTimer = null;
      setStatus('就绪');
      // 隐藏进度条
      document.querySelectorAll('.progress-bar').forEach(el => el.style.display = 'none');
      // 刷新当前步骤数据
      await refreshCurrentStep();
    }
  }, 1000);
};

const updateProgressUI = (progress) => {
  const pct = progress.total > 0 ? Math.round(progress.current / progress.total * 100) : 0;
  const msg = `${progress.message} (${pct}%)`;

  // 更新进度条
  document.querySelectorAll('.progress-bar .fill').forEach(el => {
    el.style.width = `${pct}%`;
  });
  // 更新进度文本
  document.querySelectorAll('.progress-text, #fetch-status, #analyze-status, #dedup-status').forEach(el => {
    if (el) el.textContent = msg;
  });
  // 确保进度条可见
  document.querySelectorAll('.progress-bar').forEach(el => {
    el.style.display = 'block';
  });
};

const refreshCurrentStep = async () => {
  switch (state.step) {
    case 1:
      // 从 API 拉取种子数据
      const tData = await api('/api/torrents');
      if (tData && tData.status === 'ok') {
        state.torrents = tData.torrents;
      }
      renderFetchTorrents();
      break;
    case 2:
      const pData = await api('/api/analyze/profiles');
      if (pData && pData.status === 'ok') {
        state.profiles = pData.profiles;
      }
      renderAnalyze();
      break;
    case 3:
      const dData = await api('/api/dedup/results');
      if (dData && dData.status === 'ok') {
        state.dedupResults = dData.groups;
        state.dedupOverview = dData.summary;
      }
      renderDedup();
      break;
    case 4: renderCleanup(); break;
  }
};

// ─── 侧边栏切换 ───────────────────────────────────────────────
window.switchStep = async (idx) => {
  state.step = idx;
  document.querySelectorAll('.step').forEach((el, i) => {
    el.className = 'step' + (i === idx ? ' active' : '');
  });
  // 切换步骤时先拉取最新数据
  if (idx === 1) {
    const tData = await api('/api/torrents');
    if (tData && tData.status === 'ok') state.torrents = tData.torrents;
  } else if (idx === 2) {
    const pData = await api('/api/analyze/profiles');
    if (pData && pData.status === 'ok') state.profiles = pData.profiles;
  } else if (idx === 3) {
    const dData = await api('/api/dedup/results');
    if (dData && dData.status === 'ok') {
      state.dedupResults = dData.groups;
      state.dedupOverview = dData.summary;
    }
  }
  renderContent();

  // 检查是否有后台任务正在运行，如果有则自动启动轮询
  const prog = await api('/api/progress');
  if (prog && prog.running) {
    state.busy = true;
    setStatus(prog.progress.message, true);
    startPolling();
  }
};

// ─── 主渲染 ───────────────────────────────────────────────────
const renderContent = () => {
  const content = document.getElementById('content');
  content.innerHTML = '';
  switch (state.step) {
    case 0: renderConfig(content); break;
    case 1: renderFetchTorrents(content); break;
    case 2: renderAnalyze(content); break;
    case 3: renderDedup(content); break;
    case 4: renderCleanup(content); break;
  }
};

// ═══════════════════════════════════════════════════════════════
// Step 0: 配置
// ═══════════════════════════════════════════════════════════════
const renderConfig = async (container) => {
  const data = await api('/api/config');
  if (!data) return;
  state.config = data.config;

  container.innerHTML = `
    <h2>⚙️ 配置</h2>
    <p class="desc">配置 qBittorrent 连接、SMB 挂载和去重策略</p>

    <div class="card">
      <div class="card-title">qBittorrent 连接</div>
      <div class="form-row">
        <div class="form-group">
          <label>地址</label>
          <input id="cfg-qb-host" value="${data.config.qb_host}">
        </div>
        <div class="form-group" style="max-width:100px">
          <label>端口</label>
          <input id="cfg-qb-port" value="${data.config.qb_port}">
        </div>
        <div class="form-group" style="max-width:120px">
          <label>用户名</label>
          <input id="cfg-qb-user" value="${data.config.qb_username}">
        </div>
        <div class="form-group" style="max-width:160px">
          <label>密码</label>
          <input id="cfg-qb-pass" type="password" value="${data.config.qb_password}">
        </div>
      </div>
      <div class="btn-row">
        <button class="btn" onclick="testQBConnection()">🔄 测试连接</button>
        <span id="qb-test-result" style="font-size:13px;color:#8b949e;"></span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">SMB 挂载配置</div>
      <div class="form-row">
        <div class="form-group">
          <label>SMB 地址</label>
          <input id="cfg-smb-host" value="${data.config.smb_host}">
        </div>
        <div class="form-group" style="max-width:120px">
          <label>共享名称</label>
          <input id="cfg-smb-share" value="${data.config.smb_share}">
        </div>
        <div class="form-group" style="max-width:120px">
          <label>用户名</label>
          <input id="cfg-smb-user" value="${data.config.smb_username}">
        </div>
        <div class="form-group" style="max-width:160px">
          <label>密码</label>
          <input id="cfg-smb-pass" type="password" value="${data.config.smb_password}">
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">去重策略</div>
      <div class="form-row">
        <div class="form-group" style="max-width:200px">
          <label>处理分类</label>
          <input id="cfg-categories" value="${(data.config.categories || []).join(', ')}">
          <span class="hint">多个分类用逗号分隔</span>
        </div>
        <div class="form-group" style="max-width:160px">
          <label>合集策略</label>
          <select id="cfg-collection">
            <option value="skip" ${data.config.collection_strategy === 'skip' ? 'selected' : ''}>跳过合集（保护模式）</option>
            <option value="prefer" ${data.config.collection_strategy === 'prefer' ? 'selected' : ''}>合集优先</option>
          </select>
          <span class="hint">跳过合集=不删合集种子，只处理独立种子</span>
        </div>
        <div class="form-group" style="max-width:100px">
          <label>跳过小文件</label>
          <input id="cfg-min-size" value="${data.config.min_file_size_mb}">
          <span class="hint">单位 MB</span>
        </div>
      </div>
      <div class="card-title" style="margin-top:16px">优先级链（音轨 → 字幕 → 来源 → 分辨率 → HDR）</div>
      <div class="form-row">
        <div class="form-group">
          <label>音轨</label>
          <input value="中文全景声 > 其他中文音轨 > 无" disabled style="color:#8b949e;">
        </div>
        <div class="form-group">
          <label>字幕</label>
          <input value="中文特效字幕 > 其他中文字幕 > 无" disabled style="color:#8b949e;">
        </div>
        <div class="form-group">
          <label>来源</label>
          <input value="BluRay > WEB-DL > 其他" disabled style="color:#8b949e;">
        </div>
        <div class="form-group">
          <label>分辨率</label>
          <input value="4K > 1080p > 更低" disabled style="color:#8b949e;">
        </div>
        <div class="form-group">
          <label>HDR</label>
          <input value="DV P7 > P8 > P5 > HDR10+ > HDR10 > SDR" disabled style="color:#8b949e;">
        </div>
      </div>
      <div class="btn-row">
        <button class="btn btn-primary" onclick="saveConfig()">💾 保存配置</button>
        <span id="cfg-save-result" style="font-size:13px;color:#8b949e;"></span>
      </div>
    </div>
  `;
};

window.testQBConnection = async () => {
  const el = document.getElementById('qb-test-result');
  el.textContent = '测试中...';
  const data = await api('/api/config/test-qb', {
    method: 'POST',
    body: JSON.stringify({
      qb_host: document.getElementById('cfg-qb-host').value,
      qb_port: parseInt(document.getElementById('cfg-qb-port').value),
      qb_username: document.getElementById('cfg-qb-user').value,
      qb_password: document.getElementById('cfg-qb-pass').value,
    }),
  });
  if (data) {
    el.textContent = data.status === 'ok' ? '✅ 连接成功' : `❌ ${data.message}`;
    el.style.color = data.status === 'ok' ? '#3fb950' : '#f85149';
  }
};

window.saveConfig = async () => {
  const cfg = {
    qb_host: document.getElementById('cfg-qb-host').value,
    qb_port: parseInt(document.getElementById('cfg-qb-port').value),
    qb_username: document.getElementById('cfg-qb-user').value,
    qb_password: document.getElementById('cfg-qb-pass').value,
    smb_host: document.getElementById('cfg-smb-host').value,
    smb_share: document.getElementById('cfg-smb-share').value,
    smb_username: document.getElementById('cfg-smb-user').value,
    smb_password: document.getElementById('cfg-smb-pass').value,
    categories: document.getElementById('cfg-categories').value.split(',').map(s => s.trim()).filter(Boolean),
    collection_strategy: document.getElementById('cfg-collection').value,
    min_file_size_mb: parseInt(document.getElementById('cfg-min-size').value) || 300,
  };
  const data = await api('/api/config', {
    method: 'PUT', body: JSON.stringify(cfg),
  });
  const el = document.getElementById('cfg-save-result');
  if (data && data.status === 'ok') {
    el.textContent = '✅ 已保存';
    el.style.color = '#3fb950';
    state.config = data.config;
  } else {
    el.textContent = '❌ 保存失败';
    el.style.color = '#f85149';
  }
};

// ═══════════════════════════════════════════════════════════════
// Step 1: 获取种子
// ═══════════════════════════════════════════════════════════════
const renderFetchTorrents = (container) => {
  const cats = (state.config?.categories || []).join(', ');
  const count = state.torrents.length;

  container.innerHTML = `
    <h2>📥 获取种子</h2>
    <p class="desc">从 qBittorrent 拉取种子列表，按配置的分类过滤</p>

    <div class="card">
      <div class="card-title">操作</div>
      <p style="color:#8b949e;font-size:13px;margin-bottom:8px">处理分类：${cats || '全部'}</p>
      <div class="btn-row">
        <button class="btn btn-primary" onclick="fetchTorrents()" id="btn-fetch">🚀 开始获取</button>
        <span id="fetch-status" style="font-size:13px;color:#8b949e;"></span>
      </div>
      <div class="progress-bar" id="fetch-progress" style="display:none"><div class="fill" style="width:0%"></div></div>
    </div>

    <div class="card" id="fetch-result" style="${count === 0 ? 'display:none' : ''}">
      <div class="card-title">结果</div>
      <div class="stats-row">
        <div class="stat-card"><div class="num">${count}</div><div class="label">种子总数</div></div>
        <div class="stat-card"><div class="num" id="fetch-collection-count">0</div><div class="label">合集种子</div></div>
      </div>
      <div style="max-height:400px;overflow-y:auto">
        <table>
          <thead><tr><th>名称</th><th>分类</th><th>大小</th><th>类型</th></tr></thead>
          <tbody id="fetch-tbody">
            ${state.torrents.map(t => `
              <tr>
                <td style="max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${t.name}">${t.name}</td>
                <td><span class="tag tag-blue">${t.category}</span></td>
                <td>${formatSize(t.size)}</td>
                <td>${t.is_collection ? '<span class="tag tag-gold">合集</span>' : '<span class="tag tag-gray">单集</span>'}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    </div>
  `;

  if (count > 0) {
    const coll = state.torrents.filter(t => t.is_collection).length;
    document.getElementById('fetch-collection-count').textContent = coll;
  }
};

window.fetchTorrents = async () => {
  const btn = document.getElementById('btn-fetch');
  btn.disabled = true;
  document.getElementById('fetch-status').textContent = '获取中...';
  document.getElementById('fetch-progress').style.display = 'block';
  startPolling();

  const data = await api('/api/torrents/fetch', { method: 'POST' });
  if (data && data.status === 'ok') {
    showToast('开始获取种子列表', 'success');
  } else {
    btn.disabled = false;
    showToast(data?.error || '获取失败', 'error');
  }
};

// ═══════════════════════════════════════════════════════════════
// Step 2: 深度分析
// ═══════════════════════════════════════════════════════════════
const renderAnalyze = (container) => {
  const count = state.profiles.length;

  container.innerHTML = `
    <h2>🔍 深度分析</h2>
    <p class="desc">通过 SMB 挂载读取视频文件，使用 MediaInfo 提取音轨、字幕、HDR 信息</p>

    <div class="card">
      <div class="card-title">操作</div>
      <div class="btn-row">
        <button class="btn btn-primary" onclick="startAnalyze()" id="btn-analyze" ${state.torrents.length === 0 ? 'disabled' : ''}>🔍 开始分析</button>
        <span id="analyze-status" style="font-size:13px;color:#8b949e;"></span>
      </div>
      <div class="progress-bar" id="analyze-progress" style="display:none"><div class="fill" style="width:0%"></div></div>
      <div id="analyze-progress-text" class="progress-text"></div>
    </div>

    <div class="card" id="analyze-result" style="${count === 0 ? 'display:none' : ''}">
      <div class="card-title">分析结果</div>
      <div class="stats-row">
        <div class="stat-card"><div class="num">${count}</div><div class="label">视频文件</div></div>
        <div class="stat-card"><div class="num" id="analyze-atmos">0</div><div class="label">中文全景声</div></div>
        <div class="stat-card"><div class="num" id="analyze-dv">0</div><div class="label">杜比视界</div></div>
      </div>
    </div>
  `;

  if (count > 0) {
    const atmos = state.profiles.filter(p => p.audio_level === 'chinese_atmos').length;
    const dv = state.profiles.filter(p => p.hdr_level.startsWith('dv')).length;
    document.getElementById('analyze-atmos').textContent = atmos;
    document.getElementById('analyze-dv').textContent = dv;
  }
};

window.startAnalyze = async () => {
  document.getElementById('btn-analyze').disabled = true;
  document.getElementById('analyze-status').textContent = '分析中...';
  document.getElementById('analyze-progress').style.display = 'block';
  startPolling();

  const data = await api('/api/analyze/start', { method: 'POST' });
  if (data && data.status === 'ok') {
    showToast('开始深度分析', 'success');
  } else {
    document.getElementById('btn-analyze').disabled = false;
    showToast(data?.error || '分析失败', 'error');
  }
};

// ═══════════════════════════════════════════════════════════════
// Step 3: 去重筛选
// ═══════════════════════════════════════════════════════════════
const renderDedup = (container) => {
  container.innerHTML = `
    <h2>🎯 去重筛选</h2>
    <p class="desc">按五层优先级链自动排序，标记保留/删除版本</p>

    <div class="card">
      <div class="card-title">操作</div>
      <div class="btn-row">
        <button class="btn btn-primary" onclick="runDedup()" id="btn-dedup" ${state.profiles.length === 0 ? 'disabled' : ''}>🎯 开始去重</button>
        <span id="dedup-status" style="font-size:13px;color:#8b949e;"></span>
      </div>
    </div>

    <div id="dedup-results"></div>
  `;

  if (state.dedupResults.length > 0) {
    renderDedupResults();
  }
};

const renderDedupResults = () => {
  const container = document.getElementById('dedup-results');
  const summary = state.dedupOverview;
  if (!summary) return;

  container.innerHTML = `
    <div class="stats-row">
      <div class="stat-card"><div class="num">${summary.delete_candidates}</div><div class="label">待删除</div></div>
      <div class="stat-card"><div class="num">${summary.duplicate_groups}</div><div class="label">重复组</div></div>
      <div class="stat-card"><div class="num">${summary.total_groups}</div><div class="label">总组数</div></div>
    </div>
    <div id="dedup-groups">
      ${state.dedupResults.filter(g => g.delete && g.delete.length > 0).map(g => renderDupGroup(g)).join('')}
    </div>
  `;
};

const renderDupGroup = (g) => {
  const keep = g.keep;
  const deletes = g.delete || [];
  const all = [keep, ...deletes];

  const renderItem = (p, isKeep) => {
    const key = `${p.torrent_hash}|${p.file_index}`;
    const override = state.keepOverrides[key];
    const actualKeep = override !== undefined ? override : isKeep;
    return `
      <div class="dup-item ${actualKeep ? 'keep' : 'delete'}">
        <span class="dup-badge ${actualKeep ? 'keep' : 'delete'}">${actualKeep ? '保留' : '删除'}</span>
        <div class="dup-info">
          <div class="name" title="${p.torrent_name}">${p.torrent_name}</div>
          <div class="meta">
            ${p.audio_detail ? `<span class="tag tag-green">${p.audio_detail}</span>` : ''}
            ${p.subtitle_detail ? `<span class="tag tag-gold">${p.subtitle_detail}</span>` : ''}
            <span class="tag tag-blue">${p.source_detail}</span>
            <span class="tag tag-blue">${p.resolution_detail}</span>
            <span class="tag ${p.hdr_level.startsWith('dv') ? 'tag-gold' : 'tag-gray'}">${p.hdr_detail}</span>
            ${p.is_collection ? '<span class="tag tag-red">合集</span>' : ''}
          </div>
          <div class="meta" style="color:#6e7681">${p.category} · ${formatSize(p.file_size)}</div>
        </div>
        <span class="dup-switch" onclick="toggleKeep('${key}', ${!actualKeep})">切换</span>
      </div>
    `;
  };

  // 标题
  const title = keep?.title || g.group_key;
  const year = keep?.year ? `(${keep.year})` : '';
  return `
    <div class="dup-group">
      <div class="dup-header">
        <span>${title} ${year} <span class="badge">共 ${all.length} 个版本</span></span>
        <span class="badge" style="color:${deletes.length > 0 ? '#f85149' : '#3fb950'}">
          ${deletes.length > 0 ? `删除 ${deletes.length} 个` : '无需清理'}
        </span>
      </div>
      ${all.map(p => renderItem(p, p.torrent_hash === keep?.torrent_hash && p.file_index === keep?.file_index)).join('')}
    </div>
  `;
};

window.toggleKeep = (key, newVal) => {
  state.keepOverrides[key] = newVal;
  // 重新渲染
  renderDedupResults();
};

window.runDedup = async () => {
  state.keepOverrides = {};
  document.getElementById('btn-dedup').disabled = true;
  document.getElementById('dedup-status').textContent = '计算中...';
  startPolling();

  const data = await api('/api/dedup/run', { method: 'POST' });
  if (data && data.status === 'ok') {
    showToast('开始去重计算', 'success');
  } else {
    document.getElementById('btn-dedup').disabled = false;
    showToast(data?.error || '去重失败', 'error');
  }
};

// ═══════════════════════════════════════════════════════════════
// Step 4: 清理删除
// ═══════════════════════════════════════════════════════════════
const renderCleanup = (container) => {
  const deleteList = [];
  for (const g of state.dedupResults) {
    const keep = g.keep;
    for (const p of (g.delete || [])) {
      const key = `${p.torrent_hash}|${p.file_index}`;
      const override = state.keepOverrides[key];
      // 如果用户手动切换为保留，则跳过
      if (override === true) continue;
      // 如果用户手动切换为删除，或者原本就是删除
      if (override === false || (override === undefined && p.torrent_hash !== keep?.torrent_hash)) {
        deleteList.push(p);
      }
    }
  }

  container.innerHTML = `
    <h2>🗑️ 清理删除</h2>
    <p class="desc">确认并执行删除操作</p>

    <div class="card">
      <div class="card-title">待删除概览</div>
      <div class="stats-row">
        <div class="stat-card"><div class="num" id="cleanup-count">${deleteList.length}</div><div class="label">待删除种子</div></div>
        <div class="stat-card"><div class="num" id="cleanup-size">${formatSize(deleteList.reduce((s, p) => s + (p.file_size || 0), 0))}</div><div class="label">可释放空间</div></div>
      </div>
      <div class="btn-row">
        <button class="btn btn-danger" onclick="confirmDelete()" id="btn-cleanup" ${deleteList.length === 0 ? 'disabled' : ''}>🗑️ 删除 ${deleteList.length} 个种子</button>
        <span id="cleanup-status" style="font-size:13px;color:#8b949e;"></span>
      </div>
    </div>

    <div class="card" id="cleanup-list" style="${deleteList.length === 0 ? 'display:none' : ''}">
      <div class="card-title">明细</div>
      <div style="max-height:500px;overflow-y:auto">
        <table>
          <thead><tr><th>名称</th><th>分类</th><th>大小</th><th>品质</th></tr></thead>
          <tbody>
            ${deleteList.map(p => `
              <tr>
                <td style="max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${p.torrent_name}">${p.torrent_name}</td>
                <td><span class="tag tag-blue">${p.category}</span></td>
                <td>${formatSize(p.file_size)}</td>
                <td>
                  ${p.audio_detail ? `<span class="tag tag-green">${p.audio_detail}</span>` : ''}
                  ${p.source_detail ? `<span class="tag tag-blue">${p.source_detail}</span>` : ''}
                  ${p.resolution_detail ? `<span class="tag tag-blue">${p.resolution_detail}</span>` : ''}
                  ${p.hdr_detail ? `<span class="tag tag-gray">${p.hdr_detail}</span>` : ''}
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    </div>
  `;
};

window.confirmDelete = async () => {
  // 收集需要删除的 hash
  const deleteHashes = new Set();
  for (const g of state.dedupResults) {
    const keep = g.keep;
    for (const p of (g.delete || [])) {
      const key = `${p.torrent_hash}|${p.file_index}`;
      const override = state.keepOverrides[key];
      if (override === true) continue;
      if (override === false || (override === undefined && p.torrent_hash !== keep?.torrent_hash)) {
        deleteHashes.add(p.torrent_hash);
      }
    }
  }

  const hashes = Array.from(deleteHashes);
  if (hashes.length === 0) {
    showToast('没有待删除的种子', 'info');
    return;
  }

  if (!confirm(`确认删除 ${hashes.length} 个种子？此操作不可撤销！`)) return;

  const btn = document.getElementById('btn-cleanup');
  btn.disabled = true;
  document.getElementById('cleanup-status').textContent = '删除中...';

  const data = await api('/api/torrents/delete', {
    method: 'POST',
    body: JSON.stringify({ hashes, delete_files: true }),
  });

  if (data && data.status === 'ok') {
    showToast(`成功删除 ${data.deleted} 个种子`, 'success');
    document.getElementById('cleanup-status').textContent = `✅ 已删除 ${data.deleted} 个`;
    // 从本地状态中移除
    state.torrents = state.torrents.filter(t => !hashes.includes(t.hash));
    state.profiles = state.profiles.filter(p => !hashes.includes(p.torrent_hash));
    state.dedupResults = [];
    renderCleanup();
  } else {
    btn.disabled = false;
    showToast(data?.error || '删除失败', 'error');
  }
};

// ═══════════════════════════════════════════════════════════════
// 初始化
// ═══════════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
  switchStep(0);
});