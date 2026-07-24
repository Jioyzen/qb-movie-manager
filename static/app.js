/* ─── QB 影视管理工具 v2.1 - SPA ─────────────────────────────── */
const state = { step: 0, torrents: [], matches: [], profiles: [], dedup: [], overview: null,
  config: null, busy: false, pollTimer: null, keepOverrides: {}, qbCategories: [], collectionFilter: false, tmdbFilter: false };

const api = async (url, opts = {}) => {
  try { const r = await fetch(url, { headers: { 'Content-Type': 'application/json', ...opts.headers }, ...opts }); return await r.json(); }
  catch (e) { showToast(e.message, 'error'); return null; }
};

const showToast = (msg, type = 'info') => {
  const el = document.getElementById('toast');
  el.textContent = msg; el.className = `toast ${type}`;
  clearTimeout(el._timer); el._timer = setTimeout(() => el.classList.add('hidden'), 3000);
};

const fmtSize = (b) => { if (!b) return '?'; const u = ['B','KB','MB','GB','TB']; let i=0,s=b; while(s>=1024&&i<4){s/=1024;i++} return `${s.toFixed(1)} ${u[i]}`; };

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
      document.querySelectorAll('.task-status, .progress-text').forEach(e => { if (e) e.textContent = `${d.progress.message} (${pct}%)`; });
      // TMDB 匹配时实时刷新
      if (state.step === 2 && d.current_step === 'tmdb') {
        const live = await api('/api/tmdb/live');
        if (live && live.status === 'ok') {
          const hadData = state.matches.length > 0;
          state.matches = live.matches;
          if (!hadData && live.matches.length > 0) {
            // 首次有数据，渲染整个页面
            renderTmdb(document.getElementById('content'));
          } else if (hadData && live.matches.length > 0) {
            // 已有数据，只更新统计数字和表格行
            const nonColl = state.torrents.filter(t => !t.is_collection);
            const totalSeeds = nonColl.length;
            const matched = live.matches.filter(m => m.tmdb_id && !m.tmdb_id.startsWith('protected:') && !m.is_collection).length;
            const el = document.getElementById('tmdb-stats');
            if (el) {
              el.innerHTML = `
                <div class="stat-card"><div class="num">${totalSeeds}</div><div class="label">需匹配</div></div>
                <div class="stat-card"><div class="num" style="color:#3fb950">${matched}</div><div class="label">已匹配</div></div>
                <div class="stat-card"><div class="num" style="color:#8b949e">${totalSeeds - matched}</div><div class="label">未匹配</div></div>`;
            }
            const tbody = document.getElementById('tmdb-tbody');
            if (tbody) {
              live.matches.forEach(m => {
                if (m.is_collection) return;
                const row = document.getElementById('row-' + m.torrent_hash);
                if (row && m.tmdb_id) {
                  const status = '✅';
                  const idHtml = `<span class="tag tag-green">${m.tmdb_id}</span>`;
                  row.innerHTML = `<td style="text-align:center;font-size:16px">${status}</td>
                    <td title="${m.torrent_name}" style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${m.torrent_name}</td>
                    <td>${idHtml}</td>
                    <td style="max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${m.tmdb_title_cn||'-'}</td>
                    <td>${m.tmdb_year||m.parsed_year||'-'}</td>
                    <td>${m.tmdb_rating ? '<span class="tag tag-green">' + m.tmdb_rating + '</span>' : '-'}</td>`;
                }
              });
            }
          }
        }
      }
    } else if (state.busy || d.progress.total > 0) {
      state.busy = false; clearInterval(state.pollTimer); state.pollTimer = null;
      setStatus('就绪');
      document.querySelectorAll('.progress-bar').forEach(e => e.style.display = 'none');
      await refreshCurrentStep();
    }
  }, 1000);
};

const refreshCurrentStep = async () => {
  // 拉取最新数据
  switch (state.step) {
    case 1: { const d = await api('/api/torrents'); if (d) state.torrents = d.torrents; break; }
    case 2: { const d = await api('/api/tmdb/results'); if (d) state.matches = d.matches; break; }
    case 3: { const d = await api('/api/analyze/profiles'); if (d) state.profiles = d.profiles; break; }
    case 4: { const d = await api('/api/dedup/results'); if (d) { state.dedup = d.groups; state.overview = d.summary; } break; }
  }
  // 用 renderContent 重新渲染（自动传入正确的 container）
  await renderContent();
};

window.switchStep = async (idx) => {
  state.step = idx;
  document.querySelectorAll('.step').forEach((e, i) => e.className = 'step' + (i === idx ? ' active' : ''));
  if (idx === 1) { const d = await api('/api/torrents'); if (d) state.torrents = d.torrents; }
  else if (idx === 2) { const d = await api('/api/tmdb/results'); if (d) state.matches = d.matches; }
  else if (idx === 3) { const d = await api('/api/analyze/profiles'); if (d) state.profiles = d.profiles; }
  else if (idx === 4) { const d = await api('/api/dedup/results'); if (d) { state.dedup = d.groups; state.overview = d.summary; } }
  await renderContent();
  const p = await api('/api/progress');
  if (p && p.running) { state.busy = true; startPolling(); }
};

const renderContent = async () => {
  const c = document.getElementById('content'); c.innerHTML = '';
  await ({ 0: renderConfig, 1: renderFetch, 2: renderTmdb, 3: renderAnalyze, 4: renderDedup, 5: renderCleanup })[state.step](c);
  setTimeout(fixStickyHeaders, 50);
};

// ─── 修复表头穿透 - position:fixed 克隆 ────────────────────
function fixStickyHeaders() {
  // 先清除所有残留的固定表头
  document.querySelectorAll('.sticky-header-fixed').forEach(el => el.remove());
  document.querySelectorAll('.card[style*="overflow-y:auto"]').forEach(card => {
    if (card._stickyFixed) return;
    card._stickyFixed = true;
    const table = card.querySelector('table');
    if (!table) return;
    const thead = table.querySelector('thead');
    if (!thead || !thead.querySelector('th')) return;

    // 恢复原始 thead
    thead.style.visibility = '';
    thead.style.height = '';

    // 测量 th 宽度
    const ths = thead.querySelectorAll('th');
    const widths = [];
    ths.forEach(th => widths.push(th.offsetWidth));

    // 创建固定表头
    const header = document.createElement('div');
    header.className = 'sticky-header-fixed';
    header._card = card;
    header.style.cssText = 'position:fixed;z-index:9999;background:#161b22;display:flex;pointer-events:none;';
    document.body.appendChild(header);

    // 填充表头单元格
    ths.forEach((th, i) => {
      const cell = document.createElement('div');
      cell.textContent = th.textContent;
      cell.style.cssText = `padding:8px 12px;font-size:13px;color:#8b949e;font-weight:500;border-bottom:1px solid #30363d;flex:0 0 ${widths[i]}px;box-sizing:border-box;text-align:left;`;
      header.appendChild(cell);
    });

    // 初始隐藏
    header.style.display = 'none';

    // 更新位置
    const pad = 20;
    function updatePos() {
      const rect = card.getBoundingClientRect();
      if (card.scrollTop > 0) {
        header.style.display = 'flex';
        header.style.top = rect.top + 'px';
        header.style.left = (rect.left + pad) + 'px';
        header.style.width = card.clientWidth + 'px';
      } else {
        header.style.display = 'none';
      }
    }

    // 卡片滚动监听
    card.addEventListener('scroll', () => { requestAnimationFrame(updatePos); });
  });

  // 全局页面滚动和窗口大小变化监听（仅注册一次）
  if (!window._stickyHeaderInit) {
    window._stickyHeaderInit = true;
    const contentEl = document.querySelector('.content');

    // 窗口大小变化
    window.addEventListener('resize', () => {
      document.querySelectorAll('.sticky-header-fixed').forEach(h => {
        const c = h._card;
        if (c && c.scrollTop > 0) {
          const r = c.getBoundingClientRect();
          h.style.top = r.top + 'px';
          h.style.left = (r.left + 20) + 'px';
          h.style.width = c.clientWidth + 'px';
        }
      });
    });

    // 页面内容区域滚动
    if (contentEl) {
      contentEl.addEventListener('scroll', () => {
        requestAnimationFrame(() => {
          document.querySelectorAll('.sticky-header-fixed').forEach(h => {
            const c = h._card;
            if (c) {
              const r = c.getBoundingClientRect();
              if (c.scrollTop > 0) {
                h.style.display = 'flex';
                h.style.top = r.top + 'px';
                h.style.left = (r.left + 20) + 'px';
                h.style.width = c.clientWidth + 'px';
              } else {
                h.style.display = 'none';
              }
            }
          });
        });
      });
    }
  }
}

// ═══════════════════════════════════════════════════════════════
// Step 0: 配置
// ═══════════════════════════════════════════════════════════════
const renderConfig = async (container) => {
  const d = await api('/api/config');
  if (!d) return;
  state.config = d.config;
  const cats = d.config.categories || [];
  container.innerHTML = `
    <h2>⚙️ 配置</h2>
    <p class="desc">配置完成后点击底部按钮进入下一步</p>
    <div id="config-msg" style="display:none;padding:10px 16px;border-radius:8px;margin-bottom:16px;font-size:13px;"></div>
    <div class="card"><div class="card-title">qBittorrent 连接</div>
      <div class="form-row">
        <div class="form-group"><label>地址</label><input id="c-qb-h" value="${d.config.qb_host}"></div>
        <div class="form-group" style="max-width:100px"><label>端口</label><input id="c-qb-p" value="${d.config.qb_port}"></div>
        <div class="form-group" style="max-width:120px"><label>用户名</label><input id="c-qb-u" value="${d.config.qb_username}"></div>
        <div class="form-group" style="max-width:160px"><label>密码</label><input id="c-qb-pw" type="password" value="${d.config.qb_password}"></div>
      </div>
      <div class="btn-row"><button class="btn" onclick="testQBAndFetchCats()">🔄 测试连接并获取分类</button><span id="qb-test-r" style="font-size:13px;color:#8b949e;"></span></div>
      <div id="cat-select" style="margin-top:12px;${state.qbCategories.length === 0 ? 'display:none' : ''}">
        <label style="color:#8b949e;font-size:12px;">选择需要处理的分类：</label>
        <div class="form-row" style="margin-top:6px" id="cat-checkboxes"></div>
      </div>
    </div>
    <div class="card"><div class="card-title">SMB 挂载配置</div>
      <div class="form-row">
        <div class="form-group"><label>SMB 地址</label><input id="c-sh" value="${d.config.smb_host}"></div>
        <div class="form-group" style="max-width:120px"><label>共享名称</label><input id="c-ss" value="${d.config.smb_share}"></div>
        <div class="form-group" style="max-width:120px"><label>用户名</label><input id="c-su" value="${d.config.smb_username}"></div>
        <div class="form-group" style="max-width:160px"><label>密码</label><input id="c-sp" type="password" value="${d.config.smb_password}"></div>
      </div>
    </div>
    <div class="card"><div class="card-title">TMDB 配置</div>
      <div class="form-row">
        <div class="form-group" style="max-width:300px"><label>API Key</label><input id="c-tk" value="${d.config.tmdb_api_key}"></div>
        <div class="form-group" style="max-width:100px"><label>请求间隔(秒)</label><input id="c-tr" value="${d.config.tmdb_rate_limit}"></div>
      </div>
    </div>
    <div class="card"><div class="card-title">去重策略</div>
      <div class="form-row">
        <div class="form-group" style="max-width:160px"><label>合集策略</label><select id="c-col"><option value="skip" ${d.config.collection_strategy==='skip'?'selected':''}>跳过合集（保护）</option><option value="prefer" ${d.config.collection_strategy==='prefer'?'selected':''}>合集优先</option></select></div>
        <div class="form-group" style="max-width:100px"><label>小文件阈值(MB)</label><input id="c-ms" value="${d.config.min_file_size_mb}"></div>
      </div>
    </div>
    <div class="btn-row" style="justify-content:center;margin-top:24px">
      <button class="btn btn-primary" onclick="verifyAndGo()" id="btn-go" style="font-size:15px;padding:12px 32px">✅ 保存配置，开始获取种子</button>
    </div>`;
  if (state.qbCategories.length > 0) renderCatCheckboxes();
};

const renderCatCheckboxes = () => {
  const el = document.getElementById('cat-select');
  if (!el) return;
  el.style.display = 'block';
  const box = document.getElementById('cat-checkboxes');
  const selected = state.config?.categories || [];
  box.innerHTML = state.qbCategories.map(c => `<label style="display:flex;align-items:center;gap:6px;margin-right:12px;cursor:pointer">
    <input type="checkbox" value="${c}" ${selected.includes(c) ? 'checked' : ''} onchange="toggleCat('${c}')">
    <span class="tag tag-blue">${c}</span>
  </label>`).join('');
};

window.toggleCat = (cat) => {
  const cb = document.querySelector(`#cat-checkboxes input[value="${cat}"]`);
  if (!cb) return;
  const cats = state.config?.categories || [];
  if (cb.checked) { if (!cats.includes(cat)) cats.push(cat); }
  else { const i = cats.indexOf(cat); if (i > -1) cats.splice(i, 1); }
};

window.testQBAndFetchCats = async () => {
  const el = document.getElementById('qb-test-r'); el.textContent = '测试中...';
  const d = await api('/api/config/test-qb', { method: 'POST', body: JSON.stringify({
    qb_host: document.getElementById('c-qb-h').value, qb_port: parseInt(document.getElementById('c-qb-p').value),
    qb_username: document.getElementById('c-qb-u').value, qb_password: document.getElementById('c-qb-pw').value }) });
  if (d && d.status === 'ok') {
    el.textContent = '✅ 连接成功，获取分类中...';
    // Fetch categories
    const r = await api('/api/categories');
    if (r && r.status === 'ok') {
      state.qbCategories = r.categories;
      el.textContent = `✅ 连接成功，找到 ${r.categories.length} 个分类，请勾选需要处理的分类`;
      el.style.color = '#3fb950';
      // Update config with categories
      state.config = state.config || {};
      state.config.categories = [];
      renderCatCheckboxes();
    } else {
      el.textContent = '✅ 连接成功，但获取分类失败';
      el.style.color = '#3fb950';
    }
  } else {
    el.textContent = `❌ ${d?.message || '连接失败'}`;
    el.style.color = '#f85149';
  }
};

function showConfigMsg(msg, type) {
  const el = document.getElementById('config-msg');
  if (!el) return;
  el.textContent = msg;
  el.className = type || 'error';
  el.style.display = 'block';
}

window.verifyAndGo = async () => {
  const btn = document.getElementById('btn-go'); btn.disabled = true;
  showConfigMsg('验证中...', 'success');

  // 检查是否已获取分类并勾选了至少一个
  const catCheckboxes = document.getElementById('cat-checkboxes');
  if (!catCheckboxes || catCheckboxes.querySelectorAll('input:checked').length === 0) {
    showConfigMsg('❌ 请先测试连接并至少选择一个分类', 'error');
    btn.disabled = false;
    return;
  }

  // Save config first
  await api('/api/config', { method: 'PUT', body: JSON.stringify({
    qb_host: document.getElementById('c-qb-h').value, qb_port: parseInt(document.getElementById('c-qb-p').value),
    qb_username: document.getElementById('c-qb-u').value, qb_password: document.getElementById('c-qb-pw').value,
    smb_host: document.getElementById('c-sh').value, smb_share: document.getElementById('c-ss').value,
    smb_username: document.getElementById('c-su').value, smb_password: document.getElementById('c-sp').value,
    tmdb_api_key: document.getElementById('c-tk').value, tmdb_rate_limit: parseFloat(document.getElementById('c-tr').value) || 0.2,
    categories: state.config?.categories || [],
    collection_strategy: document.getElementById('c-col').value,
    min_file_size_mb: parseInt(document.getElementById('c-ms').value) || 300 }) });

  // Verify
  const v = await api('/api/config/verify', { method: 'POST' });
  if (v && v.status === 'ok') {
    showConfigMsg('✅ 配置有效，开始获取种子', 'success');
    switchStep(1);
  } else {
    showConfigMsg(`❌ ${v?.message || '配置验证失败'}`, 'error');
    btn.disabled = false;
    showToast(v?.message || '配置验证失败', 'error');
  }
};

// ═══════════════════════════════════════════════════════════════
// Step 1: 获取种子
// ═══════════════════════════════════════════════════════════════
const renderFetch = (container) => {
  const count = state.torrents.length;
  const collCount = state.torrents.filter(t => t.is_collection).length;
  const hasData = count > 0;
  const filtered = state.collectionFilter ? state.torrents.filter(t => t.is_collection) : state.torrents;
  container.innerHTML = `
    <h2>📥 获取种子</h2>
    <p class="desc">从 qBittorrent 拉取种子列表</p>
    <div class="card"><div class="card-title">操作</div>
      <div class="btn-row"><button class="btn btn-primary" onclick="fetchTorrents()" id="btn-fetch">🚀 开始获取</button>
        <button class="btn" onclick="switchStep(2)" id="btn-next-1" ${hasData ? '' : 'disabled'}>➡️ 进入TMDB匹配</button>
        <span class="task-status" style="font-size:13px;color:#8b949e;"></span></div>
      <div class="progress-bar" style="display:none"><div class="fill" style="width:0%"></div></div>
    </div>
    ${hasData ? `
    <div class="stats-row"><div class="stat-card"><div class="num">${count}</div><div class="label">种子总数</div></div>
      <div class="stat-card" style="cursor:pointer" onclick="toggleCollectionFilter()" title="点击查看合集种子">
        <div class="num" style="color:${state.collectionFilter ? '#d29922' : '#8b949e'}">${collCount}</div>
        <div class="label">${state.collectionFilter ? '▼ 合集（点击显示全部）' : '合集'}</div>
      </div>
    </div>
    <div class="card" style="max-height:500px;overflow-y:auto;position:relative;z-index:0">
      ${state.collectionFilter ? '<div style="padding:8px 0;font-size:12px;color:#d29922">仅显示合集种子（共 ' + collCount + ' 个）</div>' : ''}
      <table><thead><tr><th style="min-width:350px">名称</th><th>分类</th><th>大小</th><th>类型</th></tr></thead>
      <tbody>${filtered.map(t => `<tr><td style="max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${t.name}">${t.name}</td><td><span class="tag tag-blue">${t.category}</span></td><td>${fmtSize(t.size)}</td><td>${t.is_collection ? '<span class="tag tag-gold">合集</span>' : '<span class="tag tag-gray">单集</span>'}</td></tr>`).join('')}</tbody>
        </table>
      </div>
    </div>` : ''}`
  ;
};

window.toggleCollectionFilter = () => {
  state.collectionFilter = !state.collectionFilter;
  renderFetch(document.getElementById('content'));
};

window.fetchTorrents = async () => {
  document.getElementById('btn-fetch').disabled = true;
  document.querySelector('.task-status').textContent = '获取中...';
  document.querySelector('.progress-bar').style.display = 'block';
  startPolling();
  const d = await api('/api/torrents/fetch', { method: 'POST' });
  if (!d || d.status !== 'ok') { document.getElementById('btn-fetch').disabled = false; showToast(d?.error || '获取失败', 'error'); }
};

// ═══════════════════════════════════════════════════════════════
// Step 2: TMDB 匹配
// ═══════════════════════════════════════════════════════════════
const renderTmdb = (container) => {
  // 始终以种子列表为基础，叠加匹配结果
  const seedMap = {};
  state.matches.forEach(m => { seedMap[m.torrent_hash] = m; });
  const displayList = state.torrents.map(t => {
    const match = seedMap[t.hash] || {};
    return {
      torrent_hash: t.hash, torrent_name: t.name, category: t.category,
      parsed_title: match.parsed_title || '', parsed_year: match.parsed_year || '',
      tmdb_id: match.tmdb_id || '', tmdb_title_cn: match.tmdb_title_cn || '',
      tmdb_title_en: match.tmdb_title_en || '', tmdb_year: match.tmdb_year || '', tmdb_rating: match.tmdb_rating || '', is_collection: t.is_collection,
    };
  }).filter(m => !m.is_collection); // 合集种子不参与匹配，不显示在列表中
  const totalSeeds = displayList.length;
  const matched = displayList.filter(m => m.tmdb_id && m.tmdb_id !== '').length;
  const unmatched = totalSeeds - matched;
  const hasData = displayList.length > 0;
  const filtered = state.tmdbFilter ? displayList.filter(m => !m.tmdb_id || m.tmdb_id === '') : displayList;
  container.innerHTML = `
    <h2>🏷️ TMDB 匹配</h2>
    <p class="desc">从种子名称提取电影名和年份，匹配 TMDB 获取电影 ID</p>
    <div class="card"><div class="card-title">操作</div>
      <div class="btn-row">
        <button class="btn btn-primary" onclick="startTmdb()" id="btn-tmdb" ${state.torrents.length === 0 ? 'disabled' : ''}>🏷️ 开始匹配</button>
        <button class="btn" onclick="togglePause()" id="btn-pause" style="display:none">⏸️ 暂停</button>
        <button class="btn" onclick="switchStep(3)" id="btn-next-2" ${hasData ? '' : 'disabled'}>➡️ 进入深度分析</button>
        <span class="task-status" style="font-size:13px;color:#8b949e;"></span></div>
      <div class="progress-bar" style="display:none"><div class="fill" style="width:0%"></div></div>
    </div>
    ${hasData ? `
    <div class="stats-row" id="tmdb-stats">
      <div class="stat-card"><div class="num">${totalSeeds}</div><div class="label">需匹配</div></div>
      <div class="stat-card"><div class="num" style="color:#3fb950">${matched}</div><div class="label">已匹配</div></div>
      <div class="stat-card" style="cursor:pointer" onclick="toggleTmdbFilter()">
        <div class="num" style="color:${state.tmdbFilter ? '#f85149' : '#8b949e'}">${unmatched}</div>
        <div class="label">${state.tmdbFilter ? '▼ 未匹配（点击显示全部）' : '未匹配'}</div>
      </div>
    </div>
    <div class="card" style="max-height:600px;overflow-y:auto;position:relative">
      ${state.tmdbFilter ? '<div style="padding:8px 0;font-size:12px;color:#f85149">仅显示未匹配种子，可手动填写 TMDB ID</div>' : ''}
      <table><thead><tr><th>状态</th><th style="min-width:300px">种子名</th><th>TMDB ID</th><th style="max-width:120px">中文名</th><th>年代</th><th>评分</th></tr></thead>
      <tbody id="tmdb-tbody">${filtered.map(m => {
        const status = m.tmdb_id ? '✅' : (state.busy ? '⏳' : '⬜');
        const rating = m.tmdb_rating ? `<span class="tag ${parseFloat(m.tmdb_rating) >= 7 ? 'tag-green' : parseFloat(m.tmdb_rating) >= 5 ? 'tag-gold' : 'tag-red'}">${m.tmdb_rating}</span>` : '-';
        return `<tr id="row-${m.torrent_hash}">
          <td style="text-align:center;font-size:16px">${status}</td>
          <td title="${m.torrent_name}" style="max-width:350px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${m.torrent_name}</td>
          <td>${m.tmdb_id
            ? `<div style="display:flex;gap:4px;align-items:center"><span class="tag tag-green" style="cursor:pointer" onclick="editTmdbId('${m.torrent_hash}')" id="tid-${m.torrent_hash}">${m.tmdb_id} ✏️</span></div>`
            : `<div style="display:flex;gap:4px;align-items:center"><input id="mid-${m.torrent_hash}" placeholder="填写ID" style="width:80px;padding:3px 6px;border:1px solid #30363d;border-radius:4px;background:#0d1117;color:#e1e4e8;font-size:12px"><button class="btn btn-sm" onclick="saveManualId('${m.torrent_hash}')">确认</button></div>`}
          </td>
          <td style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${m.tmdb_title_cn||'-'}</td>
          <td>${m.tmdb_year||m.parsed_year||m.torrent_name.match(/\b(19\d{2}|20\d{2})\b/g)?.slice(-1)[0]||'-'}</td>
          <td>${rating}</td>
        </tr>`;
      }).join('')}</tbody></table>
    </div>` : ''}`
  ;
  // 显示暂停按钮（如果正在运行）
  const bp = document.getElementById('btn-pause');
  if (bp) bp.style.display = state.busy ? 'inline-flex' : 'none';
};

window.togglePause = async () => {
  const d = await api('/api/tmdb/pause', { method: 'POST' });
  const btn = document.getElementById('btn-pause');
  if (d && btn) {
    btn.textContent = d.paused ? '▶️ 继续' : '⏸️ 暂停';
  }
};

window.toggleTmdbFilter = () => {
  state.tmdbFilter = !state.tmdbFilter;
  renderTmdb(document.getElementById('content'));
};

window.saveManualId = async (hash) => {
  const input = document.getElementById(`mid-${hash}`);
  const id = input.value.trim();
  if (!id || !/^\d+$/.test(id)) { showToast('请输入有效的 TMDB ID（数字）', 'error'); return; }
  // 先抓取电影信息
  const info = await api('/api/tmdb/fetch', { method: 'POST', body: JSON.stringify({ tmdb_id: id }) });
  if (!info || info.status !== 'ok') { showToast(`❌ ${info?.error || '未找到该电影'}`, 'error'); return; }
  // 保存到匹配结果
  const d = await api('/api/tmdb/update', { method: 'POST', body: JSON.stringify({
    torrent_hash: hash, tmdb_id: id, tmdb_title_cn: info.tmdb_title_cn,
    tmdb_title_en: info.tmdb_title_en, tmdb_rating: info.tmdb_rating || '' }) });
  if (d && d.status === 'ok') {
    showToast(`✅ 已匹配: ${info.tmdb_title_cn}`, 'success');
    const r = await api('/api/tmdb/results');
    if (r) state.matches = r.matches;
    renderTmdb(document.getElementById('content'));
  } else { showToast(`❌ ${d?.error || '更新失败'}`, 'error'); }
};

window.editTmdbId = (hash) => {
  const el = document.getElementById('tid-' + hash);
  if (!el) return;
  const currentId = el.textContent.replace(' ✏️', '').trim();
  // 将标签替换为输入框
  el.outerHTML = `<div style="display:flex;gap:4px;align-items:center" id="tid-${hash}">
    <input id="mid-${hash}" value="${currentId}" placeholder="填写ID" style="width:80px;padding:3px 6px;border:1px solid #58a6ff;border-radius:4px;background:#0d1117;color:#e1e4e8;font-size:12px">
    <button class="btn btn-sm" onclick="saveManualId('${hash}')">确认</button>
    <button class="btn btn-sm" onclick="cancelEditTmdbId('${hash}', '${currentId}')">取消</button>
  </div>`;
};

window.cancelEditTmdbId = (hash, currentId) => {
  const el = document.getElementById('tid-' + hash);
  if (!el) return;
  el.outerHTML = `<div style="display:flex;gap:4px;align-items:center" id="tid-${hash}">
    <span class="tag tag-green" style="cursor:pointer" onclick="editTmdbId('${hash}')">${currentId} ✏️</span>
  </div>`;
};

window.startTmdb = async () => {
  document.getElementById('btn-tmdb').disabled = true;
  document.querySelector('.task-status').textContent = '匹配中...';
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
  // 构建 match 查找表（torrent_hash -> match）
  const matchMap = {};
  state.matches.forEach(m => { matchMap[m.torrent_hash] = m; });

  const profileList = state.profiles.filter(p => !p.is_collection).map(p => {
    const match = matchMap[p.torrent_hash] || {};
    return {
      ...p,
      tmdb_title_cn: match.tmdb_title_cn || '',
      tmdb_year: match.tmdb_year || p.year || '',
      tmdb_rating: match.tmdb_rating || '',
    };
  });

  const atmosCount = profileList.filter(p => p.audio_level === 'chinese_atmos').length;
  const dvCount = profileList.filter(p => p.hdr_level && p.hdr_level.startsWith('dv')).length;

  container.innerHTML = `
    <h2>🔍 深度分析</h2>
    <p class="desc">通过 SMB 挂载读取视频文件，MediaInfo 提取音轨、字幕、HDR 信息</p>
    <div class="card"><div class="card-title">操作</div>
      <div class="btn-row">
        <button class="btn btn-primary" onclick="startAnalyze()" id="btn-analyze" ${state.torrents.length === 0 ? 'disabled' : ''}>🔍 开始分析</button>
        <button class="btn" onclick="switchStep(4)" id="btn-next-3" ${count > 0 ? '' : 'disabled'}>➡️ 进入去重筛选</button>
        <span class="task-status" style="font-size:13px;color:#8b949e;"></span></div>
      <div class="progress-bar" style="display:none"><div class="fill" style="width:0%"></div></div>
      <div class="progress-text" style="display:none"></div>
    </div>
    ${count > 0 ? `<div class="stats-row">
      <div class="stat-card"><div class="num">${count}</div><div class="label">视频文件</div></div>
      <div class="stat-card"><div class="num" style="color:#3fb950">${atmosCount}</div><div class="label">中文全景声</div></div>
      <div class="stat-card"><div class="num" style="color:#d29922">${dvCount}</div><div class="label">杜比视界</div></div>
    </div>
    <div class="card" style="max-height:700px;overflow-y:auto;position:relative">
      ${profileList.map(p => {
        const rating = p.tmdb_rating ? `<span class="tag ${parseFloat(p.tmdb_rating) >= 7 ? 'tag-green' : parseFloat(p.tmdb_rating) >= 5 ? 'tag-gold' : 'tag-red'}">${p.tmdb_rating}</span>` : '';
        const collTag = p.is_collection ? '<span class="tag tag-gold" style="margin-left:6px">合集</span>' : '';
        // 音轨标签
        const audioTag = p.audio_detail
          ? `<span class="tag ${p.audio_level === 'chinese_atmos' ? 'tag-green' : 'tag-blue'}">${p.audio_detail}</span>`
          : '<span class="tag tag-gray">未知</span>';
        // 字幕标签
        const subTag = p.subtitle_detail
          ? `<span class="tag tag-blue">${p.subtitle_detail}</span>`
          : '<span class="tag tag-gray">无</span>';
        // 来源标签
        const srcTag = `<span class="tag tag-blue">${p.source_detail || p.source || '未知'}</span>`;
        // 分辨率标签
        const resTag = `<span class="tag tag-blue">${p.resolution_detail || p.resolution || '未知'}</span>`;
        // HDR 标签
        const hdrTag = p.hdr_level && p.hdr_level !== 'sdr'
          ? `<span class="tag tag-gold">${p.hdr_detail || p.hdr_level}</span>`
          : '<span class="tag tag-gray">SDR</span>';

        return `<div class="profile-item">
          <div class="profile-row1">
            <span class="profile-name" title="${p.torrent_name}">${p.torrent_name}</span>
            ${(p.tags || []).map(tag => `<span class="tag ${tag === '全景声' ? 'tag-green' : tag === '特效' ? 'tag-gold' : 'tag-blue'}">${tag}</span>`).join('')}
            <span class="profile-title">${p.tmdb_title_cn || p.title || '-'}</span>
            <span class="profile-year">${p.tmdb_year || p.year || '-'}</span>
            ${rating ? `<span class="profile-rating">${rating}</span>` : ''}
            ${collTag}
          </div>
          <div class="profile-row2">
            <span class="profile-label">音频</span> ${audioTag}
            <span class="profile-label">字幕</span> ${subTag}
            <span class="profile-label">来源</span> ${srcTag}
            <span class="profile-label">分辨率</span> ${resTag}
            <span class="profile-label">HDR</span> ${hdrTag}
          </div>
        </div>`;
      }).join('')}
    </div>` : ''}`;
};

window.startAnalyze = async () => {
  document.getElementById('btn-analyze').disabled = true;
  document.querySelector('.task-status').textContent = '分析中...';
  document.querySelector('.progress-bar').style.display = 'block';
  document.querySelector('.progress-text').style.display = 'block';
  startPolling();
  const d = await api('/api/analyze/start', { method: 'POST' });
  if (!d || d.status !== 'ok') { document.getElementById('btn-analyze').disabled = false; showToast(d?.error || '分析失败', 'error'); }
};

// ═══════════════════════════════════════════════════════════════
// Step 4: 去重筛选
// ═══════════════════════════════════════════════════════════════
const DEFAULT_PRIORITY = {
  layers: ['audio','subtitle','source','resolution','hdr'],
  audio: ['chinese_atmos','chinese_audio','english_atmos','english_audio','none'],
  subtitle: ['chinese_forced','chinese_sub','none'],
  source: ['bluray','webdl','other'],
  resolution: ['2160p','1080p','other'],
  hdr: ['dv_p7','dv_p8','dv_p5','hdr10plus','hdr10','sdr'],
};

const PRIORITY_LABELS = {
  audio: { chinese_atmos:'国语全景声', chinese_audio:'国语', english_atmos:'英语全景声', english_audio:'英语', none:'其他音轨' },
  subtitle: { chinese_forced:'中文特效字幕', chinese_sub:'中文字幕', none:'其他字幕' },
  source: { bluray:'BluRay', webdl:'WEB-DL', other:'其他来源' },
  resolution: { '2160p':'4K', '1080p':'1080p', other:'其他分辨率' },
  hdr: { dv_p7:'杜比视界 P7', dv_p8:'杜比视界 P8', dv_p5:'杜比视界 P5', hdr10plus:'HDR10+', hdr10:'HDR10', sdr:'SDR' },
};

let priorityState = JSON.parse(JSON.stringify(DEFAULT_PRIORITY));

// 从后端配置恢复优先级设置
async function loadPriorityConfig() {
  if (!state.config) {
    const d = await api('/api/config');
    if (d) state.config = d.config;
  }
  if (state.config) {
    if (state.config.priority_layers) priorityState.layers = state.config.priority_layers;
    ['audio', 'subtitle', 'source', 'resolution', 'hdr'].forEach(layer => {
      if (state.config[layer]) priorityState[layer] = state.config[layer];
    });
  }
}

const renderDedup = async (container) => {
  await loadPriorityConfig();
  container.innerHTML = `
    <h2>🎯 去重筛选</h2>
    <p class="desc">配置优先级规则后执行去重操作</p>
    <div class="card" id="priority-config">
      <div class="card-title">优先级规则配置（拖拽或点击箭头调整顺序）</div>
      <div id="priority-layers" style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px"></div>
    </div>
    <div class="card"><div class="card-title">操作</div>
      <div class="btn-row">
        <button class="btn btn-primary" onclick="runDedup()" id="btn-dedup" ${state.profiles.length === 0 ? 'disabled' : ''}>🎯 开始去重</button>
        <button class="btn" onclick="switchStep(5)" id="btn-next-4" disabled>➡️ 确认选择进入清理删除</button>
        <span class="task-status" style="font-size:13px;color:#8b949e;"></span></div>
    </div>
    <div id="dedup-results"></div>`;
  renderPriorityCards();
  if (state.dedup.length > 0) renderDedupResults();
};

const LAYER_LABELS = { audio:'音轨', subtitle:'字幕', source:'来源', resolution:'分辨率', hdr:'HDR类型' };

// 保存优先级配置到后端
async function savePriorityConfig() {
  const cfg = { priority_layers: priorityState.layers };
  priorityState.layers.forEach(layer => { cfg[layer] = priorityState[layer]; });
  await api('/api/config', { method: 'PUT', body: JSON.stringify(cfg) });
}

const renderPriorityCards = () => {
  const el = document.getElementById('priority-layers');
  if (!el) return;
  el.innerHTML = priorityState.layers.map((layer, li) => {
    const items = priorityState[layer] || [];
    return `<div class="card" style="flex:1;min-width:160px;padding:12px;margin:0">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
        <button class="btn btn-sm" onclick="moveLayer(${li},-1)" ${li===0?'disabled':''} style="padding:4px 8px;font-size:14px">&#x25C0;</button>
        <span style="font-weight:600;font-size:13px;color:#f0f6fc">${LAYER_LABELS[layer]||layer}</span>
        <button class="btn btn-sm" onclick="moveLayer(${li},1)" ${li===priorityState.layers.length-1?'disabled':''} style="padding:4px 8px;font-size:14px">&#x25B6;</button>
      </div>
      <div style="display:flex;flex-direction:column;gap:3px">
        ${items.map((item, ii) => `<div class="priority-item" style="display:flex;align-items:center;justify-content:space-between;padding:4px 6px;background:#0d1117;border-radius:4px;font-size:12px;color:#c9d1d9">
          <span>${PRIORITY_LABELS[layer]?.[item]||item}</span>
          <div style="display:flex;gap:3px">
            <button class="btn btn-sm" onclick="moveItem('${layer}',${ii},-1)" ${ii===0?'disabled':''} style="padding:2px 5px;font-size:10px">&#x25B2;</button>
            <button class="btn btn-sm" onclick="moveItem('${layer}',${ii},1)" ${ii===items.length-1?'disabled':''} style="padding:2px 5px;font-size:10px">&#x25BC;</button>
          </div>
        </div>`).join('')}
      </div>
    </div>`;
  }).join('');
};

window.moveLayer = (idx, dir) => {
  const newIdx = idx + dir;
  if (newIdx < 0 || newIdx >= priorityState.layers.length) return;
  [priorityState.layers[idx], priorityState.layers[newIdx]] = [priorityState.layers[newIdx], priorityState.layers[idx]];
  renderPriorityCards();
  savePriorityConfig();
};

window.moveItem = (layer, idx, dir) => {
  const newIdx = idx + dir;
  if (newIdx < 0 || newIdx >= priorityState[layer].length) return;
  [priorityState[layer][idx], priorityState[layer][newIdx]] = [priorityState[layer][newIdx], priorityState[layer][idx]];
  renderPriorityCards();
  savePriorityConfig();
};

const renderDedupResults = () => {
  const el = document.getElementById('dedup-results');
  if (!state.overview) return;
  el.innerHTML = `
    <div class="stats-row">
      <div class="stat-card"><div class="num" style="color:#f85149">${state.overview.delete_candidates}</div><div class="label">待删除</div></div>
      <div class="stat-card"><div class="num">${state.overview.duplicate_groups}</div><div class="label">重复组</div></div>
      <div class="stat-card"><div class="num">${state.overview.total_groups}</div><div class="label">总电影</div></div>
    </div>
    <div id="dedup-groups">${state.dedup.filter(g => g.delete && g.delete.length > 0).map(g => renderDupGroup(g)).join('')}</div>`;
  const btn = document.getElementById('btn-next-4');
  if (btn) btn.disabled = false;
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
      <div class="dup-info"><div class="name" title="${p.torrent_name}">${p.torrent_name}
        ${(p.tags || []).map(tag => `<span class="tag ${tag === '全景声' ? 'tag-green' : tag === '特效' ? 'tag-gold' : 'tag-blue'}" style="margin-left:4px">${tag}</span>`).join('')}
      </div>
        <div class="meta">${p.audio_detail ? `<span class="tag tag-green">${p.audio_detail}</span>` : ''}
          ${p.subtitle_detail ? `<span class="tag tag-gold">${p.subtitle_detail}</span>` : ''}
          <span class="tag tag-blue">${p.source_detail}</span><span class="tag tag-blue">${p.resolution_detail}</span>
          <span class="tag ${p.hdr_level && p.hdr_level.startsWith('dv') ? 'tag-gold' : 'tag-gray'}">${p.hdr_detail || 'SDR'}</span>
          ${p.is_collection ? '<span class="tag tag-red">合集</span>' : ''}
        </div><div class="meta" style="color:#6e7681">${p.category} · ${fmtSize(p.file_size)}</div></div>
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
  document.querySelector('.task-status').textContent = '计算中...';
  state.busy = true;
  startPolling();
  // 发送当前优先级配置到后端
  const d = await api('/api/dedup/run', {
    method: 'POST',
    body: JSON.stringify({
      priority_layers: priorityState.layers,
      priority_order: (() => {
        const order = {};
        priorityState.layers.forEach(layer => { order[layer] = priorityState[layer]; });
        return order;
      })(),
    }),
  });
  if (!d || d.status !== 'ok') {
    document.getElementById('btn-dedup').disabled = false;
    state.busy = false;
    showToast(d?.error || '去重失败', 'error');
  }
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
        <span id="cleanup-status" style="font-size:13px;color:#8b949e;"></span></div>
      <div class="progress-bar" style="display:none"><div class="fill" style="width:0%"></div></div>
    </div>
    ${deleteList.length > 0 ? `<div class="card" style="max-height:500px;overflow-y:auto">
      <table><thead><tr><th>名称</th><th>分类</th><th>大小</th><th>品质</th></tr></thead>
      <tbody>${deleteList.map(p => `<tr><td title="${p.torrent_name}" style="max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${p.torrent_name}</td>
        <td><span class="tag tag-blue">${p.category}</span></td><td>${fmtSize(p.file_size)}</td>
        <td>${p.audio_detail ? `<span class="tag tag-green">${p.audio_detail}</span>` : ''}
            ${p.source_detail ? `<span class="tag tag-blue">${p.source_detail}</span>` : ''}
            ${p.resolution_detail ? `<span class="tag tag-blue">${p.resolution_detail}</span>` : ''}
            ${p.hdr_detail ? `<span class="tag tag-gray">${p.hdr_detail}</span>` : ''}
        </td></tr>`).join('')}</tbody></table>
    </div>` : ''}`;
};

window.confirmDelete = async () => {
  const hashes = new Set();
  for (const g of state.dedup) {
    const keep = g.keep;
    for (const p of (g.delete || [])) {
      const key = `${p.torrent_hash}|${p.file_index}`;
      const override = state.keepOverrides[key];
      if (override === true) continue;
      if (override === false || (override === undefined && p.torrent_hash !== keep?.torrent_hash)) hashes.add(p.torrent_hash);
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
  } else { document.getElementById('btn-cleanup').disabled = false; showToast(d?.error || '删除失败', 'error'); }
};

// ═══════════════════════════════════════════════════════════════
// 初始化 - 从后端恢复持久化状态
// ═══════════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', async () => {
  const restored = await api('/api/state/restore');
  if (restored && restored.has_data) {
    state.torrents = restored.torrents || [];
    state.matches = restored.tmdb_matches || [];
    state.profiles = restored.profiles || [];
    state.dedup = restored.dedup_results || [];
    // 加载配置（用于优先级规则等）
    const cfg = await api('/api/config');
    if (cfg) state.config = cfg.config;
    const savedStep = parseInt(sessionStorage.getItem('qb_step') || restored.current_step || '0');
    state.step = Math.min(savedStep, 5);
    document.querySelectorAll('.step').forEach((e, i) => e.className = 'step' + (i === state.step ? ' active' : ''));
    await renderContent();
    // 更新进度
    if (restored.progress && restored.progress.total > 0) {
      setStatus(restored.progress.message, false);
    }
  } else {
    // 没有持久化数据，显示配置页
    switchStep(0);
  }
  // 切换步骤时保存步骤到 sessionStorage
  const origSwitch = window.switchStep;
  window.switchStep = async (idx) => {
    sessionStorage.setItem('qb_step', String(idx));
    return origSwitch(idx);
  };
});