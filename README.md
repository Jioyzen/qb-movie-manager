# QB Movie Manager — 项目说明

## 项目概述

QB Movie Manager 是一个 qBittorrent 影视资源管理工具。从 qBittorrent 拉取种子列表，通过文件名解析 + TMDB 匹配自动识别每部电影，再基于品质规则找出重复资源，支持一键删除低质量副本。

## 技术栈

| 层 | 技术选型 | 原因 |
|---|---|---|
| 后端 | Flask (Python 3.12) | 轻量，无额外依赖，LAN 内网够用 |
| 前端 | Bootstrap 5 + Vanilla JS | 无需构建步骤，单 HTML 文件 |
| 文件名解析 | guessit 3.8+ | 支持 CMCT/FRDS/beAst/CHDPAD 等各大压片组命名规范 |
| 影片匹配 | TMDB API v3 | 免费 key，中文优先搜索 |
| 数据传输 | SSE (Server-Sent Events) | 替代轮询，实时推送 TMDB 匹配进度 |
| 配置持久化 | JSON 文件 | `data/config.json`，首次启动自动创建 |

## 环境依赖

- qBittorrent Web UI 已启用（地址：192.168.2.200:8085）
- TMDB API Key：f71a029311ca7a272c05c7d217bb5c5b
- Python 3.12+
- pip 包（见 requirements.txt）：flask, requests, guessit, gunicorn

## 目录结构

```
/home/zeng/qb-movie-manager/
├── backend/
│   ├── __init__.py          # 包标记，仅一行注释
│   ├── config.py            # 配置管理器：JSON 文件读写，默认值定义
│   ├── qb_client.py         # qBittorrent Web API v2 封装
│   ├── parser.py            # 文件名解析器：guessit + 正则
│   ├── tmdb_client.py       # TMDB API 客户端：串行匹配 + 重试
│   ├── dedup_engine.py      # 去重引擎：品质评分 + 保留决策
│   └── app.py               # Flask 应用：路由、SSE 端点
├── static/
│   └── js/
│       └── app.js           # 前端逻辑：步骤切换、SSE 监听、交互
├── templates/
│   └── index.html           # 前端页面：中文界面、浅色主题
├── data/
│   ├── config.json          # 持久化配置（首次保存自动创建）
│   └── .gitkeep             # 保持 data/ 目录
├── requirements.txt         # Python 依赖
└── README.md                # 本文件
```

## 流程逻辑

整体分为 5 步，UI 顶部水平导航依次进行：

### 步骤1：配置
- qBittorrent 连接参数（host/port/username/password）
- TMDB API Key
- 请求间隔（默认 0.3 秒，调大更稳）
- 并发线程数（当前强制为 1 = 串行，UI 保留字段但实际已固定）
- 跳过小文件体积（默认 300MB，小于此值的视频文件被过滤掉，通常是 Sample）
- "测试连接"验证 qBittorrent 连通性，成功后自动拉取分类列表

### 步骤2：获取种子
- 从 qBittorrent 拉取种子列表（仅"高清电影"和"4K电影"两个分类）
- 同时拉取每个种子的文件列表（qBittorrent API：torrents/files）
- 以表格展示种子名、分类、大小、分享率、状态

### 步骤3：解析与匹配
- **解析文件**：遍历所有种子文件，跳过 <300MB 的小文件，用 guessit 提取：
  - guess_title / guess_year（guessit 推断）
  - chinese_title（优先取 [方括号] 内的中文，需 >=2 个中文字符）
  - resolution / source / codec / hdr / dovi / audio_info
  - is_collection：同一种子含多个视频文件 = Y
- **编辑**：支持批量修改解析错误的标题/年份
- **TMDB 匹配**（串行执行，约 8 分钟完成 1500 条）：
  - 策略 A（有有意义中文）：中文搜索 zh-CN + year → 中文无 year → guess_title zh-CN + year
  - 策略 B（无有意义中文，如 007 系列）：guess_title en-US + year → en-US 无 year → zh-CN
  - "有意义中文"定义：`extract_chinese()` 提取出的中文字符 >=3 个（避免"系列"这种 2 字干扰）
  - 所有请求带 3 次自动重试（连接超时 / 429 限流时回溯等待）
  - SSE 实时推送匹配进度到前端，每匹配一条数据表格即时更新

### 步骤4：去重筛选
- 按 tmdb_id 分组，每组 >1 条即为重复
- 对组内条目按品质规则打分，最高分标记为 "Keep"，其余为 "Delete"
- 可切换的优先级规则：
  - 分辨率优先（2160p > 1080p > 720p）
  - 来源优先（BluRay > WEB-DL > WEBRip）
  - 编码优先（x265/H.265 > x264）
  - HDR 优先
  - 杜比视界优先
  - 多音轨优先（默认关）
  - Atmos 优先（默认关）
- 重复资源列表展示：候选删（黄色行）、保留（白色行），支持手动勾选/取消

### 步骤5：删除清理
- 汇总待删除种子数量
- **重要限制**：qBittorrent API 只能按整个种子删除，不支持按种子内单个文件删除
- 确认后调用 qBittorrent API `POST /api/v2/torrents/delete` 删除种子及磁盘文件
- 针对合集种子（如洛奇 1-6 同一种子含多部电影）的变通方案：
  1. 如果在合集种子里只想保留某几部，需手动登录 qBittorrent Web UI 删除文件 + 强制校验
  2. 如果合集内的版本质量更高，保留合集种子，删除同电影的其他单部种子
  3. 如果合集版本质量低且你想全删，直接删整个种子

## 各文件详细说明

### backend/config.py
- `Config` 类：单例对象 `config`
- 默认值：qb_host=192.168.2.200:8085, tmdb_rate_limit=0.3, tmdb_workers=1, min_file_size_mb=300
- 保存/读取 `data/config.json`
- 支持环境变量 `QB_MOVIE_CONFIG` 覆盖路径
- 提供 `qb_url` property 拼接完整 qBittorrent URL

### backend/qb_client.py
- `QBClient` 类：封装 qBittorrent Web API v2
- 自动登录（首次请求时 `/api/v2/auth/login`，session 维持状态）
- 方法：`get_categories()` / `get_torrents(category)` / `get_torrent_files(hash)` / `delete_torrents(hashes)` / `test_connection()`
- 删除时 `hashes` 用 `|` 拼接批量传参

### backend/parser.py
- `parse_filename(filename)`：核心解析函数，返回结构化 dict
- 优先用 guessit 提取 title/year/screen_size/source/video_codec/audio_codec
- 正则 fallback 提取 resolution/source/codec/HDR/DoVi/audio
- `extract_chinese(text)`：三级策略提取中文：
  1. 优先取 `[方括号]` 内的中文
  2. 取第一个 `.` 前的中文
  3. 遍历各 `.` 段取中文
  - 注意：`[007系列]` 只能提取出"系列"（2 字），会在 tmdb_client 中被判定为"无意义中文"
- `extract_first_english(text)`：提取第一个 >=3 字母的英文段
- `is_video_file(filename)`：检查是否为可播放视频文件后缀

### backend/tmdb_client.py
- `TMDBClient` 类：TMDB v3 search/movie API
- 串行匹配（`tmdb_workers=1`），不支持并发
- 每次请求间隔 `tmdb_rate_limit` 秒
- 3 次重试：连接异常 sleep 2/4s，429 限流 sleep 4/6/8s
- `_pick_best(results, query, year)`：对结果打分排序（年份精确 +100，标题完全一致 +80，部分匹配 +30）
- `_has_meaningful_chinese(text)`：中文字符 >=3 个才算有意义
- `match_entry(filename, guess_title, guess_year)`：多策略匹配，分中文路径和非中文路径
  - 中文路径：cn_zh_year → cn_zh_no_year → guess_zh_year
  - 非中文路径：en_year → en_no_year → guess_zh_year → guess_zh_no_year → fallback_en
  - 所有搜索请求都带 year 参数（如提供）
- `batch_match(entries, progress_callback)`：遍历 entries 逐个调用 match_entry，回调更新进度

### backend/dedup_engine.py
- `DedupEngine` 类
- `_score(entry)`：按激活的规则计算品质分数
  - 分辨率权重：2160p=100, 1080p=70, 720p=40
  - 来源权重：BluRay=100, WEB-DL=80, WEBRip=60
  - 编码权重：x265/H.265=100, x264=60
  - HDR 加分 15，杜比视界加分 20，多音轨 10，Atmos 15
- `get_duplicates()`：按 tmdb_id 分组，每组按分数降序排列
- `get_duplicate_summary()`：扁平列表，标记 is_keep（每组分数最高者）

### backend/app.py
- Flask 应用，路由前缀 `/api/`
- 配置端点：`GET/PUT /api/config`，`POST /api/config/test-qb`
- 数据端点：`POST /api/categories`，`POST /api/torrents`，`POST /api/torrents/files`
- 解析匹配端点：`POST /api/parse`，`POST /api/match`
- SSE 端点：`GET /api/matching/stream` — 实时推送 progress/matched/complete/error 事件
- 进度查询：`GET /api/matching/progress`
- 去重端点：`POST /api/duplicates`
- 删除端点：`POST /api/torrents/delete`
- 全局状态 `_task_state`：running/progress/result/live_results/lock，线程安全

### static/js/app.js
- Vanilla JS 约 352 行
- 步骤导航切换（`switchTab`）
- 配置加载/保存/测试连接
- 种子获取 + 文件列表（先联 `/api/torrents` 再联 `/api/torrents/files`）
- 解析（`runParse`）→ 展示解析表
- TMDB 匹配（`runMatch` + `startMatchSSE`）：EventSource 监听 SSE 事件
  - `progress`：更新进度条百分比
  - `matched`：逐条更新表格（实时显示匹配结果）
  - `complete`：关闭连接，标记完成
- 去重计算（`computeDuplicates`）→ 展示分组表格
- 全选/反选（`toggleAllDelete` / `updateDeleteCount`）
- 删除确认（`deleteSelected`）

### templates/index.html
- 中文浅色主题 Bootstrap 5 页面
- 顶部品牌栏 + 连接状态标签
- 水平 5 步导航（配置 → 获取种子 → 解析与匹配 → 去重筛选 → 删除清理）
- 配置卡分两栏：qBittorrent 连接（左）、TMDB 配置（右）
- 种子列表表格 + 解析结果表格 + 重复列表表格各带表头
- 步骤3 内含：解析按钮、TMDB 匹配按钮、进度条、编辑/保存按钮
- 步骤4 内含：优先级切换标签（点击汉堡样式的 toggle-pill）
- 步骤5 内含：待删除数量 + 确认删除按钮

## 启动方式

```bash
cd /home/zeng/qb-movie-manager
python3.12 -c "
import sys; sys.path.insert(0, '.')
from backend.app import app
app.run(host='0.0.0.0', port=5000, debug=False)
"
```

访问 http://192.168.2.56:5000

## 已处理的已知问题

1. **TMDB 并发匹配冲突**（已解决）：改为串行 + 0.3s 间隔 + 3 次重试，8 分钟内完成 1500 条
2. **种子名截断**（已解决）：移除 CSS 截断，改为 `word-break: break-word` 自动换行
3. **007 系列匹配到无关电影**（已解决）：根因是 `[007系列]` 只提取到"系列"（2 字），被判定为无意义中文，改为 >=3 字才走中文路径，否则走英文路径
4. **合集种子解析**（已解决）：按种子内每个视频文件单独解析，不是用目录名
5. **Sample 文件干扰**（已解决）：跳过 <300MB（可配置）的视频文件

## 待办 / 已知限制

- **API 无法按文件删除**：qBittorrent 的 `POST /api/v2/torrents/delete` 只能删整个种子。合集种子内单文件删除需要用户手动进 qBittorrent Web UI 操作
- **无认证机制**：Flask 应用无用户认证，仅限内网使用
- **TV Series 处理**：如"梅格雷的亡者"（Maigret's Dead Man, 2016）为 TV 剧集，当前工具仅搜索 movie 端点，可能匹配不到
- **打包部署**：暂无 Docker/打包方案，启动依赖手工命令行

## 关联文件

- `/tmp/tmdb_matcher.py`：独立 CLI 版 TMDB 匹配脚本（245 行）
- `/home/zeng/.hermes/skills/media/tmdb-smart-matching/`：Hermes skill，包含 SKILL.md 和 `scripts/tmdb_matcher.py`

## 环境信息

| 项 | 值 |
|---|---|
| 主机 IP | 192.168.2.56 |
| 服务端口 | 5000 |
| qBittorrent | 192.168.2.200:8085 (admin/zz0770) |
| TMDB API Key | f71a029311ca7a272c05c7d217bb5c5b |
| sudo 密码 | zz0770 |
| 分类 | 高清电影、4K电影 |
| 种子总数 | ~1525 |
