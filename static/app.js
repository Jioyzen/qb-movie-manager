/* ─── QB 影视管理工具 v2.0 - SPA ─────────────────────────────── */
const state = { step: 0, torrents: [], matches: [], profiles: [], dedup: [], overview: null,
  config: null, busy: false, pollTimer: null, keepOverrides: {} };

const api = async (url, opts = {}) => {
  try {
    const r = await fetch(url, { headers: { 'Content-Type': 'application/json', ...opts.headers }, ...opts });
    return await r.json();
  } catch (e) { showToast(e.message, 'error'); return null; }
};

const showToast = (msg, type = 'info') => {
  const el = document.getElementById('toast');
  el.textContent = msg; el.className = `toast ${type}`;
  clearTimeout(el._timer); el._timer = setTimeout(() => el.classList.add('hidden'), 3000);
};

const fmtSize = (b) => { if (!b) return '?'; const u = ['B','KB','MB','GB','TB']; let i=0,s=b; while(s>=1024&&i<4){s/=1024;i++} return `${s.toFixed(1)} ${u[i]}`; };
const el = (tag, a = {}, ...c) => { const e = document.createElement(tag); for(const[k,v]of Object.entries(a)){if(k==='className')e.className=v;else if(k.startsWith('on'))e.addEventListener(k.slice(2).toLowerCase(),v);else if(k==='innerHTML')e.innerHTML=v;else e.setAttribute(k,v)} for(const x of c){if(typeof x==='string')e.appendChild(document.createTextNode(x));else if(x)e.appendChild(x)} return e; };

// ─── 状态栏 ─────────────────────────────────────────────────
const setStatus = (t, b = false) => {
  document.getElementById('statusText').textContent = t;
  document.getElementById('statusDot').className = 'status-dot' + (b ? ' busy' : '');
};

// ─── 轮询 ───────────────────────────────────────────────────
const startPolling = () => {
  if (state.pollTimer) return;
  state.pollTimer = setInterval(async () => {
    const d = await api('/api/progress');
    if (!d) return;
    if (d.running) {
      state.busy = true; setStatus(d.progress.message, true);
      document.querySelectorAll('.progress-bar').forEach(e => e.style.display = 'block');
      const pct = d.progress.total > 0 ? Math.round(d.progress.current / d.progress.total * 100) : 0;
      document.querySelectorAll('.progress-bar .fill').forEach(e => e.style.width = `${pct}%`);
      document.querySelectorAll('.progress-text, .task-status').forEach(e => {
        if (e) e.textContent = `${d.progress.message} (${pct}%)`;
      });
    } else if (state.busy || d.progress.total > 0) {
      state.busy = false; clearInterval(state.pollTimer); state.pollTimer = null;
      setStatus('就绪');
      document.querySelectorAll('.progress-bar').forEach(e => e.style.display = 'none');
      await refreshCurrentStep();
    }
  }, 1500);
};

const refreshCurrentStep = async () => {
  switch (state.step) {
    case 1:
      const td = await api('/api/torrents');
      if (td && td.status === 'ok') state.torrents = td.torrents;
      renderFetchTorrents(); break;
    case 2:
      const md = await api('/api/tmdb/results');
      if (md && md.status === 'ok') state.matches = md.matches;
      renderTmdbMatch(); break;
    case 3:
      const pd = await api('/api/analyze/profiles');
      if (pd && pd.status === 'ok') state.profiles = pd.profiles;
      renderAnalyze(); break;
    case 4:
      const dd = await api('/api/dedup/results');
      if (dd && dd.status === 'ok') { state.dedup = dd.groups; state.overview = dd.summary; }
      renderDedup(); break;
    case 5: renderCleanup(); break;
  }
};

// ─── 侧边栏切换 ─────────────────────────────────────────────
window.switchStep = async (idx) => {
  state.step = idx;
  document.querySelectorAll('.step').forEach((e, i) => e.className = 'step' + (i === idx ? ' active' : ''));
  // 拉取数据
  if (idx === 1) { const d = await api('/api/torrents'); if (d) state.torrents = d.torrents; }
  else if (idx === 2) { const d = await api('/api/tmdb/results'); if (d) state.matches = d.matches; }
  else if (idx === 3) { const d = await api('/api/analyze/profiles'); if (d) state.profiles = d.profiles; }
  else if (idx === 4) { const d = await api('/api/dedup/results'); if (d) { state.dedup = d.groups; state.overview = d.summary; } }
  renderContent();
  // 检查后台任务
  const p = await api('/api/progress');
  if (p && p.running) { state.busy = true; setStatus(p.progress.message, true); startPolling(); }
};

const renderContent = () => {
  const c = document.getElementById('content'); c.innerHTML = '';
  switch (state.step) {
    case 0: renderConfig(c); break;
    case 1: renderFetchTorrents(c); break;
    case 2: renderTmdbMatch(c); break;
    case 3: renderAnalyze(c); break;
    case 4: renderDedup(c); break;
    case 5: renderCleanup(c); break;
  }
};

// ═══════════════════════════════════════════════════════════════
// Step 0: 配置
// ═══════════════════════════════════════════════════════════════
const renderConfig = async (container) => {
  const d = await api('/api/config');
  if (!d) return;
  state.config = d.config;
  container.innerHTML = `
    <h2>⚙️ 配置</h2>
    <p class="desc">配置 qBittorrent 连接、SMB 挂载和去重策略</p>
    <div class="card"><div class="card-title">qBittorrent 连接</div>
      <div class="form-row">
        <div class="form-group"><label>地址</label><input id="c-qb-h" value="${d.config.qb_host}"></div>
        <div class="form-group" style="max-width:100px"><label>端口</label><input id="c-qb-p" value="${d.config.qb_port}"></div>
        <div class="form-group" style="max-width:120px"><label>用户名</label><input id="c-qb-u" value="${d.config.qb_username}"></div>
        <div class="form-group" style="max-width:160px"><label>密码</label><input id="c-qb-pw" type="password" value="${d.config.qb_password}"></div>
      </div>
      <div class="btn-row"><button class="btn" onclick="testQB()">🔄 测试连接</button><span id="qb-test-r" style="font-size:13px;color:#8b949e;"></span></div>
    </div>
    <div class="card"><div class="card-title">SMB 挂载配置</div>
      <div class="form-row">
        <div class="form-group"><label>SMB 地址</label><input id="c-sh" value="${d.config.smb_host}"></div>
        <div class="form-group" style="max-width:120px"><label>共享名称</label><input id="c-ss" value="${d.config.smb_share}"></div>
        <div class="form-group" style="max-width:120px"><label>用户名</label><input id="c-su" value="${d.config.smb_username}"></div>
        <div class="form-group" style="max-width:160px"><label>密码</label><input id="c-sp" type="password" value="${d.config.smb_password}"></div>
      </div>
    </div>
    <div class="card"><div class="card-title">去重策略</div>
      <div class="form-row">
        <div class="form-group" style="max-width:200px"><label>处理分类</label><input id="c-cat" value="${(d.config.categories||[]).join(', ')}"><span class="hint">多个用逗号分隔</span></div>
        <div class="form-group" style="max-width:160px"><label>合集策略</label><select id="c-col"><option value="skip" ${d.config.collection_strategy==='skip'?'selected':''}>跳过合集（保护）</option><option value="prefer" ${d.config.collection_strategy==='prefer'?'selected':''}>合集优先</option></select></div>
        <div class="form-group" style="max-width:100px"><label>小文件阈值</label><input id="c-ms" value="${d.config.min_file_size_mb}"><span class="hint">MB</span></div>
      </div>
      <div class="btn-row"><button class="btn btn-primary" onclick="saveCfg()">💾 保存配置</button><span id="cfg-save-r" style="font-size:13px;color:#8b949e;"></span></div>
    </div>`;
};

window.testQB = async () => {
  const el = document.getElementById('qb-test-r'); el.textContent = '测试中...';
  const d = await api('/api/config/test-qb', { method: 'POST', body: JSON.stringify({
    qb_host: document.getElementById('c-qb-h').value, qb_port: parseInt(document.getElementById('c-qb-p').value),
    qb_username: document.getElementById('c-qb-u').value, qb_password: document.getElementById('c-qb-pw').value }) });
  if (d) { el.textContent = d.status === 'ok' ? '✅ 连接成功' : `❌ ${d.message}`; el.style.color = d.status === 'ok' ? '#3fb950' : '#f85149'; }
};

window.saveCfg = async () => {
  const d = await api('/api/config', { method: 'PUT', body: JSON.stringify({
    qb_host: document.getElementById('c-qb-h').value, qb_port: parseInt(document.getElementById('c-qb-p').value),
    qb_username: document.getElementById('c-qb-u').value, qb_password: document.getElementById('c-qb-pw').value,
    smb_host: document.getElementById('c-sh').value, smb_share: document.getElementById('c-ss').value,
    smb_username: document.getElementById('c-su').value, smb_password: document.getElementById('c-sp').value,
    categories: document.getElementById('c-cat').value.split(',').map(s => s.trim()).filter(Boolean),
    collection_strategy: document.getElementById('c-col').value,
    min_file_size_mb: parseInt(document.getElementById('c-ms').value) || 300 }) });
  const el = document.getElementById('cfg-save-r');
  if (d && d.status === 'ok') { el.textContent = '✅ 已保存'; el.style.color = '#3fb950'; state.config = d.config; }
  else { el.textContent = '❌ 保存失败'; el.style.color = '#f85149'; }
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
    <div class="card"><div class="card-title">操作</div>
      <p style="color:#8b949e;font-size:13px;margin-bottom:8px">分类：${cats || '全部'}</p>
      <div class="btn-row"><button class="btn btn-primary" onclick="fetchTorrents()" id="btn-fetch">🚀 开始获取</button>
        <span class="task-status" id="fetch-status" style="font-size:13px;color:#8b949e;"></span></div>
      <div class="progress-bar" style="display:none"><div class="fill" style="width:0%"></div></div>
    </div>
    <div class="card" id="fetch-result" style="${count === 0 ? 'display:none' : ''}">
      <div class="card-title">结果</div>
      <div class="stats-row">
        <div class="stat-card"><div class="num">${count}</div><div class="label">种子总数</div></div>
        <div class="stat-card"><div class="num" id="fetch-col">0</div><div class="label">合集</div></div>
      </div>
      <div style="max-height:500px;overflow-y:auto">
        <table><thead><tr><th>名称</th><th>分类</th><th>大小</th><th>类型</th></tr></thead>
        <tbody>${state.torrents.map(t => `<tr><td style="max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${t.name}">${t.name}</td><td><span class="tag tag-blue">${t.category}</span></td><td>${fmtSize(t.size)}</td><td>${t.is_collection ? '<span class="tag tag-gold">合集</span>' : '<span class="tag tag-gray">单集</span>'}</td></tr>`).join('')}</tbody>
        </table>
      </div>
    </div>`;
  if (count > 0) document.getElementById('fetch-col').textContent = state.torrents.filter(t => t.is_collection).length;
};

window.fetchTorrents = async () => {
  document.getElementById('btn-fetch').disabled = true;
  document.getElementById('fetch-status').textContent = '获取中...';
  document.querySelector('.progress-bar').style.display = 'block';
  startPolling();
  const d = await api('/api/torrents/fetch', { method: 'POST' });
  if (!d || d.status !== 'ok') { document.getElementById('btn-fetch').disabled = false; showToast(d?.error || '获取失败', 'error'); }
};

// ═══════════════════════════════════════════════════════════════
// Step 2: TMDB 匹配
// ═══════════════════════════════════════════════════════════════
const renderTmdbMatch = (container) => {
  const matched = state.matches.filter(m => m.tmdb_id).length;
  const total = state.matches.length;
  container.innerHTML = `
    <h2>🏷️ TMDB 匹配</h2>
    <p class="desc">从种子名称提取电影名和年份，匹配 TMDB 获取电影 ID</p>
    <div class="card"><div class="card-title">操作</div>
      <div class="btn-row"><button class="btn btn-primary" onclick="startTmdb()" id="btn-tmdb" ${state.torrents.length === 0 ? 'disabled' : ''}>🏷️ 开始匹配</button>
        <span class="task-status" id="tmdb-status" style="font-size:13px;color:#8b949e;"></span></div>
      <div class="progress-bar" style="display:none"><div class="fill" style="width:0%"></div></div>
    </div>
    ${total > 0 ? `
    <div class="stats-row"><div class="stat-card"><div class="num">${total}</div><div class="label">总种子</div></div>
      <div class="stat-card"><div class="num" style="color:#3fb950">${matched}</div><div class="label">已匹配</div></div>
      <div class="stat-card"><div class="num" style="color:#f85149">${total - matched}</div><div class="label">未匹配</div></div>
    </div>
    <div class="card" style="max-height:500px;overflow-y:auto">
      <table><thead><tr><th>种子名</th><th>TMDB ID</th><th>中文名</th><th>英文名</th></tr></thead>
      <tbody>${state.matches.map(m => `
        <tr><td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${m.torrent_name}">${m.torrent_name}</td>
        <td>${m.tmdb_id ? `<span class="tag tag-green">${m.tmdb_id}</span>` : '<span class="tag tag-red">未匹配</span>'}</td>
        <td>${m.tmdb_title_cn || '-'}</td><td>${m.tmdb_title_en || '-'}</td></tr>`).join('')}
      </tbody></table>
    </div>` : ''}
  `;
};

window.startTmdb = async () => {
  document.getElementById('btn-tmdb').disabled = true;
  document.getElementById('tmdb-status').textContent = '匹配中...';
  document.querySelector('.progress-bar').style.display = 'block';
  startPolling();
  const d = await api('/api/tmdb/match', { method: 'POST' });
  if (!d || d.status !== 'ok') { document.getElementById('btn-tmdb').disabled = false; showToast(d?.error || '匹配失败', 'error'); }
};

// ═══════════════════════════════════════════════════════════════
// Step 3: 深度分析
// ═══════════════════════════════════════════════════════════════
const renderAnalyze = (container) => {
  const count = state.profiles.length;
  container.innerHTML = `
    <h2>🔍 深度分析</h2>
    <p class="desc">通过 SMB 挂载读取视频文件，MediaInfo 提取音轨、字幕、HDR 信息</p>
    <div class="card"><div class="card-title">操作</div>
      <div class="btn-row"><button class="btn btn-primary" onclick="startAnalyze()" id="btn-analyze" ${state.torrents.length === 0 ? 'disabled' : ''}>🔍 开始分析</button>
        <span class="task-status" id="analyze-status" style="font-size:13px;color:#8b949e;"></span></div>
      <div class="progress-bar" style="display:none"><div class="fill" style="width:0%"></div></div>
      <div class="progress-text" style="display:none"></div>
    </div>
    ${count > 0 ? `
    <div class="stats-row">
      <div class="stat-card"><div class="num">${count}</div><div class="label">视频文件</div></div>
      <div class="stat-card"><div class="num" style="color:#3fb950">${state.profiles.filter(p => p.audio_level === 'chinese_atmos').length}</div><div class="label">中文全景声</div></div>
      <div class="stat-card"><div class="num" style="color:#d29922">${state.profiles.filter(p => p.hdr_level.startsWith('dv')).length}</div><div class="label">杜比视界</div></div>
    </div>` : ''}
  `;
};

window.startAnalyze = async () => {
  document.getElementById('btn-analyze').disabled = true;
  document.getElementById('analyze-status').textContent = '分析中...';
  document.querySelector('.progress-bar').style.display = 'block';
  document.querySelector('.progress-text').style.display = 'block';
  startPolling();
  const d = await api('/api/analyze/start', { method: 'POST' });
  if (!d || d.status !== 'ok') { document.getElementById('btn-analyze').disabled = false; showToast(d?.error || '分析失败', 'error'); }
};

// ═══════════════════════════════════════════════════════════════
// Step 4: 去重筛选
// ═══════════════════════════════════════════════════════════════
const renderDedup = (container) => {
  container.innerHTML = `
    <h2>🎯 去重筛选</h2>
    <p class="desc">按 TMDB ID 分组，五层优先级链自动排序，手动可切换保留版本</p>
    <div class="card"><div class="card-title">操作</div>
      <div class="btn-row"><button class="btn btn-primary" onclick="runDedup()" id="btn-dedup" ${state.profiles.length === 0 ? 'disabled' : ''}>🎯 开始去重</button>
        <span class="task-status" id="dedup-status" style="font-size:13px;color:#8b949e;"></span></div>
    </div>
    <div id="dedup-results"></div>`;
  if (state.dedup.length > 0) renderDedupResults();
};

const renderDedupResults = () => {
  const container = document.getElementById('dedup-results');
  if (!state.overview) return;
  container.innerHTML = `
    <div class="stats-row">
      <div class="stat-card"><div class="num" style="color:#f85149">${state.overview.delete_candidates}</div><div class="label">待删除</div></div>
      <div class="stat-card"><div class="num">${state.overview.duplicate_groups}</div><div class="label">重复组</div></div>
      <div class="stat-card"><div class="num">${state.overview.total_groups}</div><div class="label">总电影</div></div>
    </div>
    <div id="dedup-groups">${state.dedup.filter(g => g.delete && g.delete.length > 0).map(g => renderDupGroup(g)).join('')}</div>`;
};

const renderDupGroup = (g) => {
  const keep = g.keep, deletes = g.delete || [];
  const all = [keep, ...deletes];
  const title = g.tmdb_title_cn || keep?.title || g.group_key;
  const year = keep?.year ? `(${keep.year})` : '';

  const renderItem = (p, isKeep) => {
    const key = `${p.torrent_hash}|${p.file_index}`;
    const actualKeep = state.keepOverrides[key] !== undefined ? state.keepOverrides[key] : isKeep;
    return `<div class="dup-item ${actualKeep ? 'keep' : 'delete'}">
      <span class="dup-badge ${actualKeep ? 'keep' : 'delete'}">${actualKeep ? '保留' : '删除'}</span>
      <div class="dup-info"><div class="name" title="${p.torrent_name}">${p.torrent_name}</div>
        <div class="meta">${p.audio_detail ? `<span class="tag tag-green">${p.audio_detail}</span>` : ''}
          ${p.subtitle_detail ? `<span class="tag tag-gold">${p.subtitle_detail}</span>` : ''}
          <span class="tag tag-blue">${p.source_detail}</span><span class="tag tag-blue">${p.resolution_detail}</span>
          <span class="tag ${p.hdr_level.startsWith('dv') ? 'tag-gold' : 'tag-gray'}">${p.hdr_detail}</span>
          ${p.is_collection ? '<span class="tag tag-red">合集</span>' : ''}
        </div><div class="meta" style="color:#6e7681">${p.category} · ${fmtSize(p.file_size)}</div>
      </div>
      <span class="dup-switch" onclick="toggleKeep('${key}', ${!actualKeep})">切换</span>
    </div>`;
  };

  return `<div class="dup-group"><div class="dup-header">
    <span>${title} ${year} <span class="badge">共 ${all.length} 个版本</span></span>
    <span class="badge" style="color:${deletes.length > 0 ? '#f85149' : '#3fb950'}">${deletes.length > 0 ? `删除 ${deletes.length} 个` : '无需清理'}</span>
  </div>${all.map(p => renderItem(p, p.torrent_hash === keep?.torrent_hash && p.file_index === keep?.file_index)).join('')}</div>`;
};

window.toggleKeep = (key, val) => { state.keepOverrides[key] = val; renderDedupResults(); };

window.runDedup = async () => {
  state.keepOverrides = {};
  document.getElementById('btn-dedup').disabled = true;
  document.getElementById('dedup-status').textContent = '计算中...';
  startPolling();
  const d = await api('/api/dedup/run', { method: 'POST' });
  if (!d || d.status !== 'ok') { document.getElementById('btn-dedup').disabled = false; showToast(d?.error || '去重失败', 'error'); }
};

// ═══════════════════════════════════════════════════════════════
// Step 5: 清理删除
// ═══════════════════════════════════════════════════════════════
const renderCleanup = (container) => {
  const deleteList = [];
  for (const g of state.dedup) {
    const keep = g.keep;
    for (const p of (g.delete || [])) {
      const key = `${p.torrent_hash}|${p.file_index}`;
      const override = state.keepOverrides[key];
      if (override === true) continue;
      if (override === false || (override === undefined && p.torrent_hash !== keep?.torrent_hash)) {
        // Deduplicate by hash
        if (!deleteList.find(x => x.torrent_hash === p.torrent_hash)) deleteList.push(p);
      }
    }
  }
  const totalSize = deleteList.reduce((s, p) => s + (p.file_size || 0), 0);
  container.innerHTML = `
    <h2>🗑️ 清理删除</h2>
    <p class="desc">确认并执行删除操作</p>
    <div class="card"><div class="card-title">待删除概览</div>
      <div class="stats-row">
        <div class="stat-card"><div class="num" style="color:#f85149">${deleteList.length}</div><div class="label">待删除种子</div></div>
        <div class="stat-card"><div class="num">${fmtSize(totalSize)}</div><div class="label">可释放空间</div></div>
      </div>
      <div class="btn-row">
        <button class="btn btn-danger" onclick="confirmDelete()" id="btn-cleanup" ${deleteList.length === 0 ? 'disabled' : ''}>🗑️ 删除 ${deleteList.length} 个种子</button>
        <span id="cleanup-status" style="font-size:13px;color:#8b949e;"></span>
      </div>
      <div class="progress-bar" style="display:none"><div class="fill" style="width:0%"></div></div>
    </div>
    ${deleteList.length > 0 ? `
    <div class="card" style="max-height:500px;overflow-y:auto">
      <table><thead><tr><th>名称</th><th>分类</th><th>大小</th><th>品质</th></tr></thead>
      <tbody>${deleteList.map(p => `<tr><td style="max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${p.torrent_name}">${p.torrent_name}</td>
        <td><span class="tag tag-blue">${p.category}</span></td>
        <td>${fmtSize(p.file_size)}</td>
        <td>${p.audio_detail ? `<span class="tag tag-green">${p.audio_detail}</span>` : ''}
            ${p.source_detail ? `<span class="tag tag-blue">${p.source_detail}</span>` : ''}
            ${p.resolution_detail ? `<span class="tag tag-blue">${p.resolution_detail}</span>` : ''}
            ${p.hdr_detail ? `<span class="tag tag-gray">${p.hdr_detail}</span>` : ''}
        </td></tr>`).join('')}</tbody>
      </table>
    </div>` : ''}
  `;
};

window.confirmDelete = async () => {
  const hashes = new Set();
  for (const g of state.dedup) {
    const keep = g.keep;
    for (const p of (g.delete || [])) {
      const key = `${p.torrent_hash}|${p.file_index}`;
      const override = state.keepOverrides[key];
      if (override === true) continue;
      if (override === false || (override === undefined && p.torrent_hash !== keep?.torrent_hash)) {
        hashes.add(p.torrent_hash);
      }
    }
  }
  const arr = Array.from(hashes);
  if (arr.length === 0) { showToast('没有待删除的种子', 'info'); return; }
  if (!confirm(`确认删除 ${arr.length} 个种子？此操作不可撤销！`)) return;
  document.getElementById('btn-cleanup').disabled = true;
  document.getElementById('cleanup-status').textContent = '删除中...';
  document.querySelector('.progress-bar').style.display = 'block';
  const d = await api('/api/torrents/delete', { method: 'POST', body: JSON.stringify({ hashes: arr, delete_files: true }) });
  if (d && d.status === 'ok') {
    showToast(`成功删除 ${d.deleted} 个种子`, 'success');
    document.getElementById('cleanup-status').textContent = `✅ 已删除 ${d.deleted} 个`;
    state.torrents = state.torrents.filter(t => !arr.includes(t.hash));
    state.profiles = state.profiles.filter(p => !arr.includes(p.torrent_hash));
    state.dedup = [];
    renderCleanup();
  } else {
    document.getElementById('btn-cleanup').disabled = false;
    showToast(d?.error || '删除失败', 'error');
  }
};

// ═══════════════════════════════════════════════════════════════
// 初始化
// ═══════════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', async () => {
  // 检查是否有后台任务正在运行
  const p = await api('/api/status');
  if (p) {
    if (p.running) { state.busy = true; setStatus(p.progress.message, true); startPolling(); }
    if (p.has_torrents) { const d = await api('/api/torrents'); if (d) state.torrents = d.torrents; }
    if (p.has_tmdb) { const d = await api('/api/tmdb/results'); if (d) state.matches = d.matches; }
    if (p.has_profiles) { const d = await api('/api/analyze/profiles'); if (d) state.profiles = d.profiles; }
    if (p.has_dedup) { const d = await api('/api/dedup/results'); if (d) { state.dedup = d.groups; state.overview = d.summary; } }
  }
  switchStep(0);
});