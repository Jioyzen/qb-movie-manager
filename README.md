# QB 影视管理工具 (QB Movie Manager)

## 项目概述

一个基于 qBittorrent + TMDB 的影视资源去重管理工具。自动扫描 qBittorrent 种子库，通过多层级优先级规则（音轨→字幕→来源→分辨率→HDR）筛选同一电影的最佳版本，清理重复资源，释放硬盘空间。

---

## 设计目标

### 核心目标
从 qBittorrent 的种子列表中，找出同一部电影的不同版本，按照可自定义的优先级规则自动保留最佳版本，删除其余冗余版本。

### 为什么要做这个工具
- PT/BT 玩家常下载同一部电影的多个版本（不同小组、不同分辨率、不同压制参数）
- 手动对比筛选效率极低，上千个种子根本看不完
- 文件名解析无法获取准确的音视频信息，需要 MediaInfo 深度分析
- 需要一种灵活可配置的优先级规则，适应不同用户的需求

---

## 功能需求

### 已完成功能

| 功能 | 状态 | 说明 |
|------|------|------|
| qBittorrent 连接管理 | ✅ | 地址/端口/用户名/密码配置，连接测试 |
| 分类获取与选择 | ✅ | 测试连接时自动获取 QB 分类，用户手动勾选需处理的分类 |
| 种子列表获取 | ✅ | 按分类拉取种子，显示名称/大小/类型 |
| 合集种子检测 | ✅ | 通过 QB API 获取文件列表，统计视频文件数量（≥300MB），≥2个不同文件判定为合集 |
| 花絮/删减片段过滤 | ✅ | 排除 `删减片段.mkv`、`sample`、`trailer` 等非正片文件 |
| 分卷电影识别 | ✅ | Part.1+Part.2 等分卷电影不被误判为合集 |
| 合集保护模式 | ✅ | 合集种子不参与 TMDB 匹配和深度分析，自动标记为保留 |
| TMDB 自动匹配 | ✅ | 多策略匹配（中文→英文→文件名首段→罗马数字转换→版本号过滤） |
| 手动 TMDB ID 填写 | ✅ | 匹配失败的种子可手动输入 TMDB ID，自动抓取电影信息 |
| 暂停/继续匹配 | ✅ | 匹配过程中可暂停，修改后继续 |
| 实时匹配进度 | ✅ | 实时显示当前匹配进度、统计数字、每行匹配状态 |
| SMB 挂载 | ✅ | 自动挂载 QB 下载目录，读取视频文件 |
| MediaInfo 深度分析 | ✅ | 提取音轨语言/格式、字幕语言/类型、HDR 类型（DV P5/P7/P8、HDR10+、HDR10） |
| MediaInfo 缓存 | ✅ | 按文件路径+大小+修改时间缓存分析结果 |
| 可排序优先级规则 | ✅ | 5 层优先级卡片（音轨→字幕→来源→分辨率→HDR），每层内部可排序 |
| 去重决策引擎 | ✅ | 按 TMDB ID 分组，五层链逐层比较，选中最佳版本 |
| 手动切换保留版本 | ✅ | 去重结果中可手动切换保留/删除 |
| 批量删除种子 | ✅ | 删除种子及文件，释放硬盘空间 |
| 配置持久化 | ✅ | JSON 配置文件，含密码掩码处理 |
| 深色主题 UI | ✅ | 暗色主题，响应式布局 |

### 待完成/待优化功能

| 功能 | 优先级 | 说明 |
|------|--------|------|
| 表头 sticky 穿透 | 高 | 滚动时内容穿透表头，`z-index` 和 `isolation` 尝试无效 |
| 列表实时刷新 | 高 | 某些情况下获取种子/匹配完成后列表不自动刷新 |
| 深度分析进度 | 中 | 分析过程中的实时进度显示不够流畅 |
| 并发 TMDB 匹配 | 中 | 目前串行匹配，1570 个种子需约 2-3 分钟 |
| 并发 MediaInfo 分析 | 中 | 目前串行分析，1570 个种子需约 1 小时 |
| 网络错误重试 | 低 | TMDB 请求超时时的重试机制 |
| 操作日志 | 低 | 记录删除操作历史 |

---

## 工作流程

```
配置 → 获取种子 → TMDB 匹配 → 深度分析 → 去重筛选 → 清理删除
```

### 步骤详解

**1. 配置**
- 配置 qBittorrent 连接（地址/端口/用户名/密码）
- 点击"测试连接并获取分类"，自动拉取 QB 分类列表
- 手动勾选需要处理的分类（如"4K电影"、"高清电影"）
- 配置 SMB 挂载信息（地址/共享名/用户名/密码）
- 配置 TMDB API Key 和请求间隔
- 配置合集策略（跳过保护/合集优先）和小文件阈值
- 点击"保存配置，进入获取种子"，验证 SMB 挂载和 TMDB Key，通过后自动跳转

**2. 获取种子**
- 从 qBittorrent 拉取选中分类下的所有种子
- 通过 QB API 获取每个种子的文件列表，统计视频文件数量
- 判定合集种子（≥2个视频文件且非分卷电影）
- 显示种子列表，可点击"合集"数字筛选查看

**3. TMDB 匹配**
- 对每个非合集种子，用 guessit 解析文件名提取标题+年份
- 多策略匹配 TMDB：中文 → 英文 → 混合 → 文件名首段
- 自动转换罗马数字（Ⅱ→2、Ⅰ→1）
- 自动过滤版本号（V2、REPACK、PROPER 等）
- 合集种子自动标记为"合集保护"
- 支持实时暂停/继续，每行状态实时更新（⬜→⏳→✅/🛡️）
- 未匹配种子可手动填写 TMDB ID，自动抓取电影信息
- 统计：需匹配 / 已匹配 / 未匹配 / 合集保护

**4. 深度分析**
- 通过 SMB 挂载访问 QB 下载目录
- 对每个非合集种子，用 mediainfo CLI 提取音视频信息
- 分析内容：音轨语言/格式（Atmos 检测）、字幕语言/类型（特效/强制）、HDR 类型（DV P5/P7/P8、HDR10+、HDR10）
- 结果缓存到 `data/cache/`（按文件路径+大小+修改时间哈希）
- 合集种子仅创建最小 profile（文件名分析，无需 SMB/MediaInfo）

**5. 去重筛选**
- 按 TMDB ID 分组（未匹配的按 title+year 分组）
- 可配置 5 层优先级规则（音轨→字幕→来源→分辨率→HDR）
- 每层内部可排序（如音频：中文全景声 > 中文音轨 > 英文全景声 > 英文音轨 > 其他）
- 每组显示所有版本，自动标记保留/删除
- 可手动切换保留版本
- 合集保护：合集种子自动保留，仅在独立种子间去重
- 合集优先：合集版本优于独立版本

**6. 清理删除**
- 显示所有待删除种子及可释放空间
- 用户确认后调用 QB API 删除种子及文件
- 从本地缓存中移除已删除数据

---

## 技术架构

### 系统架构

```
┌──────────────────────────────────────────────────┐
│  浏览器前端 (SPA)                                │
│  HTML + CSS + Vanilla JS                        │
│  ┌──────────┬──────────────────────────────┐     │
│  │  Sidebar  │      Content Area            │     │
│  │  6 steps  │      Dynamic rendering       │     │
│  └──────────┴──────────────────────────────┘     │
└──────────────────────┬───────────────────────────┘
                       │ HTTP API (JSON)
┌──────────────────────┴───────────────────────────┐
│  Flask 后端                                       │
│  ┌──────────┬──────────┬──────────┬──────────┐   │
│  │  Config   │  QB API  │  TMDB   │  Media   │   │
│  │  Manager  │  Client  │  Client  │  Info    │   │
│  └──────────┴──────────┴──────────┴──────────┘   │
│  ┌──────────┬──────────┬──────────┐             │
│  │  Scoring  │  Dedup   │  Parser  │             │
│  │  Engine   │  Engine  │  (guessit)│            │
│  └──────────┴──────────┴──────────┘             │
└──────────────────────┬───────────────────────────┘
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
   qBittorrent      TMDB API      SMB Share
   Web API v2                    (MediaInfo)
```

### 后端模块

| 模块 | 职责 | 关键类/函数 |
|------|------|------------|
| `app.py` | Flask 路由、全局状态管理、后台任务调度 | `_task_state`, `_background_task()`, `_progress_callback()` |
| `config.py` | 配置加载/保存/掩码 | `Config` 类, `DEFAULTS` |
| `qb_client.py` | qBittorrent Web API 封装 | `QBClient` 类 |
| `parser.py` | guessit 文件名解析 | `parse_filename()`, `extract_chinese()` |
| `tmdb_client.py` | TMDB API 搜索/匹配 | `TMDBClient` 类, `match_entry()`, `search()`, `fetch_by_id()` |
| `scoring_engine.py` | 五层优先级评分模型 | `MediaProfile` 数据类, `rank_profiles()` |
| `media_analyzer.py` | SMB 挂载 + MediaInfo 分析 | `analyze_torrents()`, `_detect_hdr_level()`, `_detect_audio_level()` |
| `dedup_engine.py` | 去重决策引擎 | `DedupEngine` 类, `DedupResult` 类 |

### 关键数据流

```
1. 获取种子 → torrents[] (raw)
2. TMDB 匹配 → tmdb_matches[] (含 tmdb_id, tmdb_title_cn, tmdb_year, parsed_year)
3. 深度分析 → profiles[] (MediaProfile 对象)
4. 去重计算 → dedup_results[] (group_key, keep, delete[])
5. 清理删除 → 调用 QB API 删除种子
```

### 全局状态 (`_task_state`)

```python
_task_state = {
    "running": bool,        # 是否有后台任务在运行
    "paused": bool,         # TMDB 匹配是否暂停
    "current_step": str,    # 当前步骤: fetch|tmdb|analyze|dedup
    "progress": {"current": int, "total": int, "message": str},
    "torrents": [],         # 种子列表
    "tmdb_matches": [],     # TMDB 匹配结果
    "profiles": [],         # MediaProfile 列表
    "dedup_results": [],    # 去重结果
    "collection_flags": {}, # {torrent_hash: bool}
    "lock": threading.Lock(),
}
```

### 优先级规则配置

```json
{
  "layers": ["audio", "subtitle", "source", "resolution", "hdr"],
  "audio": ["chinese_atmos", "chinese_audio", "english_atmos", "english_audio", "other"],
  "subtitle": ["chinese_forced", "chinese_sub", "english_forced", "english_sub", "none"],
  "source": ["bluray", "webdl", "other"],
  "resolution": ["2160p", "1080p", "other"],
  "hdr": ["dv_p7", "dv_p8", "dv_p5", "hdr10plus", "hdr10", "sdr"]
}
```

---

## 文件结构

```
/home/zeng/qb-movie-manager/
├── app.py                    # Flask 主应用，API 路由，后台任务调度
├── config.py                 # 配置加载/保存/默认值
├── qb_client.py              # qBittorrent Web API v2 客户端
├── parser.py                 # guessit 文件名解析，中文提取
├── tmdb_client.py            # TMDB 搜索/匹配/ID 查询
├── scoring_engine.py         # 五层优先级评分模型，MediaProfile 数据类
├── media_analyzer.py         # SMB 挂载管理，MediaInfo 深度分析，缓存
├── dedup_engine.py           # 去重分组/决策/结果
├── requirements.txt          # Flask, requests, guessit
├── README.md                 # 项目文档
│
├── templates/
│   └── index.html            # 前端 SPA 入口（6 步骤侧边栏）
│
├── static/
│   ├── app.css               # 深色主题样式
│   └── app.js                # 前端 SPA 逻辑（轮询、渲染、API 调用）
│
├── data/
│   ├── config.json           # 用户配置（QB/SMB/TMDB/策略）
│   ├── .gitkeep
│   └── cache/                # MediaInfo 分析缓存（.gitignore）
│       └── *.json            # 按文件路径+大小+修改时间哈希
│
└── .gitignore                # 排除 __pycache__, .pyc, cache/, *.log
```

---

## 已知问题

### 1. 表头 sticky 穿透
**现象：** 滚动表格时，`tbody` 内容穿透 `thead` 显示在表头之上。
**尝试过的修复：**
- `th { position: sticky; top: 0; z-index: 10; }` → 无效
- `thead { position: sticky; top: 0; z-index: 20; }` → 更糟
- `th { z-index: 100; }` → 无效
- `div.card { position: relative; isolation: isolate; }` → 无效
- 容器 div 添加 `position:relative; isolation:isolate` 内联样式 → 无效
**可能的原因：** 父容器 `overflow: auto` 创建的层叠上下文与 `sticky` 的交互问题。
**建议方案：** 改用 JavaScript 监听滚动事件，动态切换表头样式，或用 `position: fixed` 模拟固定表头。

### 2. 列表刷新问题
**现象：** 获取种子后，列表不自动显示，需要切换到其他页面再切回来。
**可能的原因：** `refreshCurrentStep` 中的 `renderContent()` 调用时机问题。
**修复进展：** 已修复 `refreshCurrentStep` 调用 `renderContent()` 而非直接调用 render 函数。

### 3. 深度分析耗时
**现象：** 1570 个种子，每个需要 mediainfo 分析约 2-3 秒，总共约 1 小时。
**优化方案：** 添加并发线程池，限制并发数（如 4-8 线程），注意 SMB 连接限制。

### 4. TMDB 匹配耗时
**现象：** 1570 个种子，每个约 0.3 秒（含速率限制），总共约 8 分钟。
**优化方案：** 降低速率限制到 0.05 秒（TMDB 允许 40 请求/10 秒），或使用多线程。

---

## 配置示例 (`data/config.json`)

```json
{
  "qb_host": "192.168.2.200",
  "qb_port": 8085,
  "qb_username": "admin",
  "qb_password": "zz0770",
  "tmdb_api_key": "f71a029311ca7a272c05c7d217bb5c5b",
  "tmdb_rate_limit": 0.2,
  "tmdb_workers": 1,
  "min_file_size_mb": 300,
  "categories": ["4K电影", "高清电影"],
  "smb_host": "192.168.2.200",
  "smb_share": "media",
  "smb_username": "zeng",
  "smb_password": "Zz198903+",
  "smb_mount_point": "/mnt/qb_downloads",
  "qb_download_prefix": "/downloads",
  "collection_strategy": "skip",
  "priority_layers": ["audio", "subtitle", "source", "resolution", "hdr"]
}
```

---

## 开发环境

```bash
# 启动服务
cd /home/zeng/qb-movie-manager
python3 app.py

# 访问
http://192.168.2.222:5000

# 依赖
pip install flask requests guessit
sudo apt-get install mediainfo

# SMB 挂载（自动）
sudo mkdir -p /mnt/qb_downloads
sudo mount -t cifs //192.168.2.200/media /mnt/qb_downloads \
  -o username=zeng,password=Zz198903+,iocharset=utf8

# 密码
sudo 密码: zz0770
```