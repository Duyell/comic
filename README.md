<div align="center">
  <h1>JMComic Downloader API</h1>
  <p>
  <strong>禁漫天堂下载器 Web API 服务 — 基于 <a href="https://github.com/hect0x7/JMComic-Crawler-Python">JMComic-Crawler-Python</a> 构建</strong>
  </p>
</div>

## 简介

本项目是基于 [hect0x7/JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python) 封装的 Web API 服务。

提供 HTTP 接口和 Web 界面，提交漫画 ID 即可下载并合并为 PDF，支持多任务并发和 V2Ray 代理。

### 功能

- **Web 页面** — 浏览器访问即可使用，无需命令行
- **下载并合并 PDF** — 图片下载完成后自动合并为 PDF
- **浏览器模式 / 服务器模式** — 下载到本机或保存到服务器硬盘
- **多任务并发** — 支持同时下载多本漫画（默认最多 3 个）
- **任务状态查询** — 实时查看下载进度
- **V2Ray 代理** — 中国大陆网络环境需要配置代理访问

## 快速开始

### 安装依赖

```bash
pip install jmcomic fastapi uvicorn img2pdf
```

### 启动服务

```bash
python usage/api_server.py
```

打开浏览器访问 `http://localhost:7000` 即可。

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `JM_API_PORT` | 端口 | `7000` |
| `JM_API_MAX_WORKERS` | 最大并发下载数 | `3` |
| `JM_API_SAVE_DIR` | 服务器保存路径 | `~/jmcomic_downloads` |
| `JM_API_OPTION` | 配置文件路径 | `assets/option/option_api_server.yml` |

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | Web 前端页面 |
| `POST` | `/download` | 提交下载任务 |
| `GET` | `/status/{id}` | 查询任务状态 |
| `GET` | `/tasks` | 查看所有任务 |
| `GET` | `/download/{task_id}` | 下载 PDF |
| `GET` | `/workers` | 查看并发状态 |

### 提交下载

```bash
curl -X POST http://localhost:7000/download \
  -H "Content-Type: application/json" \
  -d '{"id": "438516", "type": "album", "browser": true}'
```

## 代理配置

中国大陆网络需要配置 V2Ray 代理，编辑 `assets/option/option_api_server.yml`：

```yaml
proxies:
  http: socks5h://127.0.0.1:10808
  https: socks5h://127.0.0.1:10808
```

## Cloudflare Tunnel 部署

```bash
cloudflared tunnel run --token <你的token>
```

## 手机运行（Termux）

```bash
pkg install python git
pip install jmcomic fastapi uvicorn img2pdf
git clone https://github.com/Duyell/comic.git
cd comic
python usage/api_server.py
```

## 致谢

- [hect0x7/JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python) — 本项目依赖的 jmcomic 核心库
