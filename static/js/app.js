// QB Movie Manager - Frontend App
const STATE = {
  config: {}, categories: [], torrents: [], torrentFiles: {},
  entries: [], matchedEntries: [], duplicates: [],
  deleteHashes: new Set(), editingAll: false, matchRunning: false,
};

document.querySelectorAll('.step').forEach(el => {
  el.addEventListener('click', () => switchTab(el.dataset.tab));
});
loadConfig();

function $(id) { return document.getElementById(id); }

function switchTab(name) {
  document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
  document.querySelector('.step[data-tab="'+name+'"]').classList.add('active');
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  const el = $('tab-'+name); if (el) el.classList.add('active');
}

// Config
function loadConfig() {
  fetch('/api/config').then(r => r.json()).then(cfg => {
    STATE.config = cfg;
    $('cfg-qb-host').value = cfg.qb_host || '';
    $('cfg-qb-port').value = cfg.qb_port || 8085;
    $('cfg-qb-user').value = cfg.qb_username || '';
    $('cfg-qb-pass').value = cfg.qb_password || '';
    $('cfg-tmdb-key').value = cfg.tmdb_api_key || '';
    $('cfg-tmdb-rate').value = cfg.tmdb_rate_limit || 0.3;
    $('cfg-tmdb-workers').value = cfg.tmdb_workers || 1;
    $('cfg-min-size').value = cfg.min_file_size_mb || 300;
    if (cfg.qb_host) testConnection();
  });
}

function saveConfig() {
  const data = {
    qb_host: $('cfg-qb-host').value,
    qb_port: parseInt($('cfg-qb-port').value) || 8085,
    qb_username: $('cfg-qb-user').value,
    qb_password: $('cfg-qb-pass').value,
    tmdb_api_key: $('cfg-tmdb-key').value,
    tmdb_rate_limit: parseFloat($('cfg-tmdb-rate').value) || 0.3,
    tmdb_workers: parseInt($('cfg-tmdb-workers').value) || 1,
    min_file_size_mb: parseInt($('cfg-min-size').value) || 300,
  };
  fetch('/api/config', {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
    .then(r => r.json()).then(() => { STATE.config = data; showStatus('testResult','配置已保存','success'); });
}

function testConnection() {
  const data = {
    qb_host: $('cfg-qb-host').value,
    qb_port: parseInt($('cfg-qb-port').value) || 8085,
    qb_username: $('cfg-qb-user').value,
    qb_password: $('cfg-qb-pass').value,
  };
  showStatus('testResult','正在测试连接...','info');
  setConnectionStatus('testing');
  fetch('/api/config/test-qb', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
    .then(r => r.json()).then(res => {
      if (res.status === 'ok') {
        showStatus('testResult','连接成功: '+res.message,'success');
        setConnectionStatus('connected');
        fetchCategories();
      } else {
        showStatus('testResult','连接失败: '+res.message,'danger');
        setConnectionStatus('error');
      }
    });
}

function setConnectionStatus(status) {
  const el = $('connectionStatus');
  if (status === 'connected') { el.className = 'status-badge bg-success text-white'; el.textContent = '已连接'; }
  else if (status === 'testing') { el.className = 'status-badge bg-warning text-dark'; el.textContent = '测试中...'; }
  else { el.className = 'status-badge bg-secondary text-white'; el.textContent = '未连接'; }
}

function showStatus(id, msg, type) {
  const el = $(id); if(!el) return;
  const cls = type==='success' ? 'text-success' : type==='danger' ? 'text-danger' : type==='info' ? 'text-info' : 'text-muted';
  el.innerHTML = '<span class="'+cls+'">'+escapeHtml(msg)+'</span>';
}

// Categories
function fetchCategories() {
  fetch('/api/categories').then(r => r.json()).then(res => {
    if(res.error) return;
    STATE.categories = res.categories || [];
    const c = $('categoryCheckboxes');
    c.innerHTML = STATE.categories.map(cat =>
      '<div class="form-check form-check-inline">'+
      '<input class="form-check-input" type="checkbox" value="'+escapeHtml(cat)+'" id="cat-'+escapeHtml(cat)+'" checked>'+
      '<label class="form-check-label" for="cat-'+escapeHtml(cat)+'">'+escapeHtml(cat)+'</label></div>'
    ).join('');
  });
}

function getSelectedCategories() {
  return Array.from(document.querySelectorAll('#categoryCheckboxes input:checked')).map(cb => cb.value);
}

// Torrents
function fetchTorrents() {
  const cats = getSelectedCategories();
  if(!cats.length) { alert('请至少选择一个分类'); return; }
  showStatus('torrentCount','正在获取种子列表...','info');
  fetch('/api/torrents', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({categories:cats})})
    .then(r => r.json()).then(res => {
      if(res.error) { showStatus('torrentCount','获取失败: '+res.error,'danger'); return; }
      STATE.torrents = res.torrents || [];
      showStatus('torrentCount',res.count+' 个种子已获取','success');
      renderTorrentTable();
      return fetchTorrentFiles();
    });
}

function fetchTorrentFiles() {
  const hashes = STATE.torrents.map(t => t.hash).filter(h => h);
  if(!hashes.length) return;
  return fetch('/api/torrents/files',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({hashes:hashes})})
    .then(r => r.json()).then(res => { if(!res.error) STATE.torrentFiles = res.files || {}; });
}

function renderTorrentTable() {
  $('torrentTbody').innerHTML = STATE.torrents.map(t =>
    '<tr>'+
    '<td><div class="cell-text">'+escapeHtml(t.name||'')+'</div></td>'+
    '<td>'+escapeHtml(t.category||'')+'</td>'+
    '<td>'+formatSize(t.total_size)+'</td>'+
'<td>'+(t.ratio||0).toFixed(2)+'</td>'+
    '<td><span class="status-badge bg-light text-muted">'+escapeHtml(t.state||'')+'</span></td>'+
    '</tr>'
  ).join('');
}

// Parse & Match
function runParse() {
  if(!STATE.torrents.length) { alert('Please fetch torrents first'); return; }
  showStatus('entryCount','Parsing...','info');
  fetch('/api/parse', {method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({torrents:STATE.torrents, torrent_files:STATE.torrentFiles})})
    .then(r => r.json()).then(res => {
      if(res.error) { showStatus('entryCount','Error: '+res.error,'danger'); return; }
      STATE.entries = res.entries || [];
      showStatus('entryCount',res.count+' entries parsed','success');
      renderEntryTable();
      switchTab('parse');
    });
}

function renderEntryTable() {
  const tbody = document.getElementById('entryTbody');
  tbody.innerHTML = STATE.entries.map((e,i) => {
    const hasTmdb = e.tmdb_id ? true : false;
    let titleHtml = '<small>'+escapeHtml(e.guess_title||'')+'</small>';
    let yearHtml = e.guess_year || '-';
    if(STATE.editingAll) {
      titleHtml = '<input class="form-control form-control-sm edit-input" data-idx="'+i+'" data-field="guess_title" value="'+escapeHtml(e.guess_title||'')+'" style="width:120px">';
      yearHtml = '<input class="form-control form-control-sm edit-input" data-idx="'+i+'" data-field="guess_year" value="'+escapeHtml(e.guess_year||'')+'" style="width:55px">';
    }
    return '<tr><td><div class="cell-text">'+escapeHtml(e.seed_name||'')+'</div></td>'+
      '<td>'+titleHtml+'</td><td>'+yearHtml+'</td>'+
      '<td>'+(e.resolution||'-')+'</td><td>'+(e.source||'-')+'</td><td>'+(e.codec||'-')+'</td>'+
      '<td>'+(e.hdr||'-')+'</td><td>'+(e.dovi||'-')+'</td>'+
      '<td><small>'+(e.audio_info||'-')+'</small></td>'+
      '<td><small>'+(hasTmdb?escapeHtml(e.tmdb_title_cn||e.tmdb_title_en||''):'-')+'</small></td>'+
      '<td>'+(hasTmdb?'<span class="badge bg-primary">'+e.tmdb_id+'</span>':'-')+'</td></tr>';
  }).join('');
}

function editAllToggle() {
  STATE.editingAll = !STATE.editingAll;
  renderEntryTable();
}

function saveEdited() {
  document.querySelectorAll('.edit-input').forEach(inp => {
    const idx = parseInt(inp.dataset.idx);
    const field = inp.dataset.field;
    if(!isNaN(idx) && field && STATE.entries[idx]) STATE.entries[idx][field] = inp.value;
  });
  showStatus('matchStatus','Edits saved');
}

// TMDB Match - SSE streaming
let matchEventSource = null;

function runMatch() {
  if(!STATE.entries.length) { alert('请先解析文件'); return; }
  if(STATE.matchRunning) { alert('正在匹配中'); return; }
  $('matchProgress').style.display = 'block';
  $('matchBar').style.width = '0%';
  $('matchBar').textContent = '0%';
  showStatus('matchStatus','正在启动匹配...','info');
  $('matchBtn').disabled = true;
  STATE.matchRunning = true;
  startMatchSSE();
  fetch('/api/match',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({entries:STATE.entries})})
    .then(r => r.json()).then(res => {
      if(res.error) {
        showStatus('matchStatus','错误: '+res.error,'danger');
        $('matchBtn').disabled = false;
        STATE.matchRunning = false;
        if(matchEventSource) { matchEventSource.close(); matchEventSource = null; }
      }
    });
}

function startMatchSSE() {
  if(matchEventSource) matchEventSource.close();
  matchEventSource = new EventSource('/api/matching/stream');

  matchEventSource.addEventListener('progress', function(e) {
    try {
      const p = JSON.parse(e.data);
      const cur = p.current || 0;
      const tot = p.total || 1;
      const pct = Math.min(100, Math.round(cur/tot*100));
      $('matchBar').style.width = pct+'%';
      $('matchBar').textContent = pct+'% ('+cur+'/'+tot+')';
      showStatus('matchStatus', p.message||'', 'info');
    } catch(ex) {}
  });

  matchEventSource.addEventListener('matched', function(e) {
    try {
      const data = JSON.parse(e.data);
      const idx = data.index;
      if(STATE.entries[idx]) {
        STATE.entries[idx].tmdb_id = data.tmdb_id;
        STATE.entries[idx].tmdb_title_cn = data.tmdb_title_cn;
        STATE.entries[idx].tmdb_title_en = data.tmdb_title_en;
        STATE.entries[idx].matched_by = data.matched_by;
        renderEntryTable();
      }
    } catch(ex) {}
  });

  matchEventSource.addEventListener('complete', function() {
    if(matchEventSource) { matchEventSource.close(); matchEventSource = null; }
    STATE.matchRunning = false;
    STATE.matchedEntries = STATE.entries.slice();
    $('matchBtn').disabled = false;
    $('matchBar').style.width = '100%';
    $('matchBar').textContent = '完成';
    const matched = STATE.entries.filter(e => e.tmdb_id).length;
    showStatus('matchStatus', '匹配完成: '+matched+'/'+STATE.entries.length, 'success');
  });

  matchEventSource.addEventListener('error', function() {
    if(matchEventSource && matchEventSource.readyState === EventSource.CLOSED) {
      STATE.matchRunning = false;
      $('matchBtn').disabled = false;
    }
  });
}

// Dedup Rules
function toggleRule(el) {
  const rule = el.dataset.rule;
  STATE.rules = STATE.rules || {};
  STATE.rules[rule] = !STATE.rules[rule];
  el.classList.toggle('active');
  el.innerHTML = '<i class="bi '+(STATE.rules[rule]?'bi-check-circle-fill text-primary':'bi-circle')+'"></i> '+el.textContent.trim();
}

function computeDuplicates() {
  if(!STATE.entries.length || !STATE.entries[0].tmdb_id) { alert('Run TMDB match first!'); return; }
  fetch('/api/duplicates',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({entries:STATE.entries,priority_rules:STATE.rules||{}})})
    .then(r => r.json()).then(res => {
      if(res.error) { showStatus('duplicateStats','Error: '+res.error,'danger'); return; }
      STATE.duplicates = res.duplicates || [];
      STATE.deleteHashes = new Set();
      document.getElementById('duplicateStats').innerHTML =
        '<span class="badge bg-info text-dark">'+(res.group_count||0)+' groups</span> '+
        '<span class="badge bg-warning text-dark">'+(res.delete_candidates||0)+' to delete</span>';
      document.getElementById('deleteCount').textContent = res.delete_candidates||0;
      renderDuplicates();
      switchTab('duplicates');
    });
}

function renderDuplicates() {
  let prevTid = '';
  document.getElementById('duplicateTbody').innerHTML = STATE.duplicates.map(e => {
    const isNewGroup = e.tmdb_id !== prevTid;
    prevTid = e.tmdb_id;
    const rowClass = e.is_keep ? '' : 'table-warning';
    const checked = e.is_keep ? '' : 'checked';
    return '<tr class="'+rowClass+'"><td><input type="checkbox" class="delete-cb" data-hash="'+e.hash+'" '+checked+'></td>'+
      '<td>'+(isNewGroup?'<strong>'+escapeHtml(e.tmdb_title_display||e.tmdb_title_cn||e.tmdb_title_en||'')+'</strong>':'')+'</td>'+
      '<td><div class="cell-text">'+escapeHtml(e.seed_name||'')+'</div></td>'+
      '<td>'+(e.resolution||'')+'</td><td>'+(e.source||'')+'</td><td>'+(e.codec||'')+'</td>'+
      '<td>'+(e.dovi?'DV ':'')+(e.hdr||'')+'</td><td><small>'+(e.audio_info||'')+'</small></td>'+
      '<td>'+(e.size_gb||0)+'GB</td><td><span class="badge bg-info text-dark">'+(e.score||0)+'</span></td>'+
      '<td><span class="badge '+(e.is_keep?'bg-success':'bg-danger')+'">'+(e.is_keep?'Keep':'Delete')+'</span></td></tr>';
  }).join('');
  updateDeleteCount();
}

function toggleAllDelete(cb) {
  document.querySelectorAll('.delete-cb').forEach(c => c.checked = cb.checked);
  updateDeleteCount();
}

function updateDeleteCount() {
  const n = document.querySelectorAll('.delete-cb:checked').length;
  document.getElementById('deleteCount').textContent = n;
  STATE.deleteHashes = new Set(Array.from(document.querySelectorAll('.delete-cb:checked')).map(c => c.dataset.hash));
}

document.addEventListener('change', e => {
  if(e.target.classList.contains('delete-cb')) {
    const h = e.target.dataset.hash;
    if(e.target.checked) STATE.deleteHashes.add(h); else STATE.deleteHashes.delete(h);
    document.getElementById('deleteCount').textContent = STATE.deleteHashes.size;
  }
});

// Delete
function deleteSelected() {
  const hashes = Array.from(STATE.deleteHashes);
  if(!hashes.length) { alert('Select torrents to delete'); return; }
  if(!confirm('Delete '+hashes.length+' torrents and files? This is irreversible!')) return;
  fetch('/api/torrents/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({hashes:hashes,delete_files:true})})
    .then(r => r.json()).then(res => {
      if(res.error) { showStatus('deleteResult','Error: '+res.error,'danger'); return; }
      showStatus('deleteResult','Deleted '+res.deleted+' torrents','success');
      STATE.entries = STATE.entries.filter(e => !STATE.deleteHashes.has(e.hash));
      STATE.deleteHashes.clear();
      document.getElementById('deleteCount').textContent = '0';
      document.getElementById('deleteFinalCount').textContent = '0';
    });
}

// Utilities
function escapeHtml(s) {
  if(!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function formatSize(bytes) {
  if(!bytes) return '0 B';
  const units = ['B','KB','MB','GB','TB'];
  let i=0, s=bytes;
  while(s>=1024 && i<units.length-1) { s/=1024; i++; }
  return s.toFixed(1)+' '+units[i];
}
