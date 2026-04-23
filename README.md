# X Downloader

一个用于本地下载公开视频的双端项目：

- `client/`：Swift 客户端，支持 iOS / macOS
- `server/`：Python FastAPI 服务端，负责解析、下载、存储和任务状态管理

## 工作方式

客户端默认连接本机服务端：`http://127.0.0.1:8000`。

典型流程：

1. 客户端提交分享链接到服务端
2. 服务端解析媒体信息
3. 服务端执行下载并保存到本地目录
4. 客户端轮询任务状态并展示结果

## 仓库结构

```text
client/   Swift 客户端
server/   FastAPI 服务端
```

## 运行前置条件

### 服务端

- Python 3.12+
- `yt-dlp`
- `ffmpeg`

建议先确认以下命令可用：

```bash
python3 --version
yt-dlp --version
ffmpeg -version
```

### 客户端

- Xcode 15+
- iOS 17+ / macOS 14+

## 服务端启动

进入 `server/` 目录后安装依赖并启动 FastAPI：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

如果需要让局域网内其他设备访问，把 `--host` 改为 `0.0.0.0`，并把客户端中的服务端地址改成你的局域网 IP。

## 客户端启动

可以用 Xcode 打开 `client/XDownloader.xcodeproj` 运行，也可以在 `client/` 下使用 SwiftPM / XCTest 做开发。

客户端默认 API 地址定义在：

- `client/Sources/AppCore/Models.swift:193`

默认值为：

```text
http://127.0.0.1:8000
```

## 默认数据目录

服务端默认会把文件写到以下位置：

- 下载产物：`~/Downloads/XDownloader`
- 数据库：`server/data/app.db`

这些默认值可通过环境变量覆盖。

## 环境变量

服务端支持以下环境变量：

- `XDL_APP_NAME`
- `XDL_ENV`
- `XDL_BOOTSTRAP_CODE`
- `XDL_DATA_DIR`
- `XDL_DATABASE_PATH`
- `XDL_ARTIFACTS_DIR`
- `XDL_YT_DLP_BINARY`
- `XDL_FFMPEG_BINARY`
- `XDL_YOUTUBE_COOKIES_FROM_BROWSER`
- `XDL_YOUTUBE_JS_RUNTIME`
- `XDL_YOUTUBE_REMOTE_COMPONENTS`
- `XDL_PROVIDER_TIMEOUT`
- `XDL_DOWNLOAD_MAX_BYTES`
- `XDL_WORKER_ENABLED`
- `XDL_WORKER_MAX_JOBS`
- `XDL_REGISTER_RATE_LIMIT`
- `XDL_REGISTER_WINDOW_SECONDS`

以下示例假设当前在 `server/` 目录内：

```bash
export XDL_DATABASE_PATH="$PWD/data/app.db"
export XDL_ARTIFACTS_DIR="$HOME/Downloads/XDownloader"
export XDL_BOOTSTRAP_CODE="change-me"
```

`XDL_BOOTSTRAP_CODE` 用于设备注册引导校验；如果你启用了这类校验，就需要设置它。本地开发若未启用对应限制，可以留空。

## 开源前说明

本仓库已忽略以下本地文件：

- `.env`、`.env.*`
- `.venv/`
- `server/data/*.db*`
- `server/data/artifacts/`
- `client/.build/`
- `client/.deriveddata/`
- `client/.swiftpm/`
- `*.xcuserdata/`
- `*.xcuserstate`
- `cookies*.txt`
- `.claude/`

如果你在本地运行过项目，请在提交前再次确认没有把运行时数据和个人文件加入版本控制。

## License

本项目使用 MIT License，详见 `LICENSE`。

## 注意事项

- 项目依赖 `yt-dlp` 和 `ffmpeg`，缺失时下载流程无法正常工作。
- 默认配置面向本地开发；如果客户端与服务端不在同一台机器，需要显式改服务端监听地址和客户端 API 地址。
- 不建议把真实数据库、下载文件、cookies 或本地会话目录提交到 GitHub。
