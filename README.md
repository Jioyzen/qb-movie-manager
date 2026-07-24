# QB 影视去重工具 (QB Movie Manager)

> 自动扫描 qBittorrent 种子库，智能识别同一电影的不同版本，按优先级规则保留最佳版本，清理冗余文件，释放硬盘空间。

[![Python](https://img.shields.io/badge/Python-3.8+-blue)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.0+-green)](https://flask.palletsprojects.com)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

---

## 目录

- [用户痛点](#用户痛点)
- [产品功能](#产品功能)
- [技术优势](#技术优势)
- [效果展示](#效果展示)
- [快速开始](#快速开始)
- [部署指南](#部署指南)
- [FAQ](#faq)

---

## 用户痛点

### 🤯 PT/BT 玩家的重复困境

玩 PT/BT 的你，是否也遇到过这些问题？

| 问题 | 描述 |
|------|------|
| **重复下载** | 同一部电影下了三四遍 — 4K 版、Remux 版、压制的、不同小组的，硬盘空间不知不觉就满了 |
| **手动对比累死人** | 想删掉差的版本，但上千个种子，手动对比分辨率、音轨、HDR、字幕太痛苦了 |
| **文件名不可信** | 文件名写着"4K HDR"，实际可能是 SDR 冒充的；写着"全景声"，可能只有 AC3 |
| **合集搞不清** | 遇到《哈利波特》8部合集，分不清是合集还是单部电影的多文件分卷 |
| **工具体验差** | 现有工具要么太复杂，要么不支持中文场景，要么需要命令行操作 |

### 🎯 这个工具能做什么

**一句话：识别同一电影的所有版本，帮你选出最好的，删掉多余的。**

```
《盗梦空间》Inception (2010)
├── 🏆 保留: Inception.2010.2160p.BluRay.x265.TrueHD.Atmos.7.1-HDS
│             → 国语全景声 + 中文字幕 + 4K + Dolby Vision P7
│
├── 🗑️ 删除: Inception.2010.1080p.BluRay.x264.DTS-HD.MA.5.1-HDS
│             → 被 4K 版击败（分辨率低）
│
└── 🗑️ 删除: Inception.2010.2160p.WEB-DL.x265.AC3.5.1
              → 被 BluRay 版击败（片源质量差）
```

---

## 产品功能

### 6 步工作流，3 分钟搞定

```
配置 → 获取种子 → TMDB 匹配 → 深度分析 → 去重筛选 → 清理删除
```

每一步都有实时进度反馈，Web 界面操作，无需命令行。

### 核心功能一览

#### 🎬 智能匹配
- **多策略 TMDB 匹配**：中文名 → 英文名 → 文件名首段，自动转换罗马数字（Ⅱ→2），自动过滤版本号（V2、REPACK、PROPER）
- **实时进度**：匹配过程实时显示每行状态（⬜→⏳→✅），支持暂停/继续
- **手动补录**：匹配失败的种子可手动输入 TMDB ID，自动抓取电影信息

#### 🔬 深度分析
- **MediaInfo 引擎**：通过 SMB 挂载读取 NAS 上的文件，提取真实音视频信息
- **音轨检测**：识别国语/英语、TrueHD/Atmos/AC3、全景声标记
- **字幕检测**：识别中文字幕、特效字幕（ASS）、强制字幕
- **HDR 识别**：精确区分 Dolby Vision P5/P7/P8、HDR10+、HDR10、SDR
- **结果缓存**：按文件哈希缓存，重复运行秒出结果

#### ⚖️ 智能去重
- **5 层优先级链**：音轨 > 字幕 > 来源 > 分辨率 > HDR，逐层比较，第一层分出胜负即停止
- **可自定义规则**：每层优先级顺序可自由调整
- **合集智能处理**：自动识别合集种子（≥2个视频文件），支持"跳过保护"和"合集优先"两种策略
- **手动干预**：去重结果可一键切换保留/删除

#### 🧹 清理删除
- 显示所有待删除种子及可释放空间
- 调用 qBittorrent API 删除种子及文件
- 一键批量清理

---

## 技术优势

| 特性 | 本项目 | 其他方案 |
|------|--------|----------|
| **中文文件名支持** | ✅ 专为 PT/BT 中文场景优化 | ❌ 英文为主，中文识别差 |
| **MediaInfo 深度分析** | ✅ 真实读取文件元数据 | ❌ 仅靠文件名猜测 |
| **Dolby Vision 精确识别** | ✅ 区分 P5/P7/P8 | ❌ 统一标为 DV |
| **合集保护** | ✅ 自动识别合集，避免误删 | ❌ 无此功能 |
| **Web 界面** | ✅ 浏览器操作，无需命令行 | ⚠️ 部分需 CLI |
| **实时进度反馈** | ✅ 每步实时显示 | ❌ 黑盒运行 |
| **优先级可定制** | ✅ 自由调整各层顺序 | ❌ 固定规则 |
| **SMB 远程分析** | ✅ 支持 NAS 远程挂载 | ⚠️ 需本地文件 |
| **单文件部署** | ✅ 一个 `python3 app.py` 搞定 | ⚠️ 需复杂环境配置 |

---

## 快速开始

### 环境要求

- **Linux 主机**（推荐 Ubuntu/Debian，需要 `sudo` 挂载 SMB）
- **Python 3.8+**
- **qBittorrent**（已运行，启用 Web UI）
- **TMDB API Key**（[免费申请](https://www.themoviedb.org/settings/api)）
- **MediaInfo**（系统级：`sudo apt-get install mediainfo`）
- 可选：SMB/CIFS 共享的 NAS 存储（如果 MediaInfo 需要在远程文件上运行）

### 安装

```bash
# 1. 克隆仓库
git clone https://github.com/Jioyzen/qb-movie-manager.git
cd qb-movie-manager

# 2. 安装 Python 依赖
pip install -r requirements.txt

# 3. 安装 MediaInfo
sudo apt-get install mediainfo

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 填入你的 QB、TMDB、SMB 信息
vim .env

# 5. 启动
python3 app.py
```

### 首次使用

1. 浏览器访问 `http://你的IP:5000`
2. 进入 **配置** 页面，点击"测试连接并获取分类"
3. 勾选需要处理的分类（如"4K电影"、"高清电影"）
4. 点击"保存配置，开始获取种子"
5. 依次完成 6 个步骤，即可清理重复资源

---

## 部署指南

### 开发环境

```bash
python3 app.py
# 监听 http://0.0.0.0:5000
```

### 生产环境（gunicorn）

```bash
pip install gunicorn
gunicorn -w 1 -b 0.0.0.0:5000 app:app
```

> 注意：使用单 worker 模式，因为应用使用全局状态和线程锁。

### 环境变量配置

创建 `.env` 文件（参见 `.env.example`），或在系统环境变量中设置：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `QB_HOST` | qBittorrent 地址 | `192.168.1.100` |
| `QB_PORT` | qBittorrent Web UI 端口 | `8085` |
| `QB_USERNAME` | qBittorrent 用户名 | `admin` |
| `QB_PASSWORD` | qBittorrent 密码 | — |
| `TMDB_API_KEY` | TMDB API Key（必填） | — |
| `SMB_HOST` | SMB/NAS 地址 | `192.168.1.100` |
| `SMB_SHARE` | SMB 共享名 | `downloads` |
| `SMB_MOUNT_POINT` | 本地挂载点 | `/mnt/qb_downloads` |
| `QB_DOWNLOAD_PREFIX` | QB 下载路径前缀 | `/downloads` |

### 配置持久化

所有配置（包括运行时通过 UI 修改的）保存在 `data/config.json`。密码在 API 响应中自动掩码，但存储在磁盘上为明文，请确保文件权限正确。

### 数据目录

```
data/
├── config.json        # 运行时配置（UI 可修改）
├── config.example.json # 配置模板
├── state.json          # 步骤状态持久化（自动保存）
└── cache/              # MediaInfo 分析缓存
    └── *.json          # 按文件路径+大小+修改时间哈希
```

---

## FAQ

### QB 连接不上？

1. 确认 qBittorrent 已启用 Web UI（设置 → Web UI → 勾选"Web 用户界面"）
2. 确认端口号正确（默认 8085）
3. 确认用户名密码正确

### TMDB 匹配失败？

1. 确认 TMDB API Key 有效（[申请地址](https://www.themoviedb.org/settings/api)）
2. 中文名匹配失败可尝试手动输入 TMDB ID
3. TMDB 有速率限制，可在配置中调整 `tmdb_rate_limit`

### 深度分析很慢？

- 首次运行需要逐个文件 mediainfo 分析，约 2-3 秒/个
- 分析结果会缓存到 `data/cache/`，下次运行直接复用
- 合集种子仅做文件名分析，跳过 MediaInfo

### 合集种子会误删吗？

- 默认 `collection_strategy: "skip"` 模式下，合集种子自动保留，不参与去重
- 可在配置中切换到 `"prefer"` 模式（合集版本优先于独立版本）

---

## 技术栈

- **后端**：Flask (Python 3)
- **前端**：Vanilla JS SPA，暗色主题 CSS
- **外部 API**：qBittorrent Web API v2、TMDB v3 API、mediainfo CLI
- **依赖**：Flask、requests、guessit、gunicorn

---

## License

MIT

---

## 致谢

- [TMDB](https://www.themoviedb.org/) 提供电影数据 API
- [qBittorrent](https://www.qbittorrent.org/) 提供下载管理 API
- [MediaInfo](https://mediaarea.net/en/MediaInfo) 提供多媒体文件分析
- [guessit](https://github.com/guessit-io/guessit) 提供文件名解析