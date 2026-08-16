# asr-tasks 基于 FunASR 的异步长视频转写服务

基于 FunASR 的异步长视频转写服务。API 接收上传文件或 HTTP(S) 地址，立即返回任务 ID；后台 Worker 使用上下文 Paraformer、FSMN-VAD 和标点模型处理普通话长音视频，并持续保存进度、心跳与部分结果。

## 快速启动

```bash
docker pull ghcr.io/hafbit/asr-tasks:latest

docker run --rm --platform linux/amd64 \
  -p 8000:8000 \
  -e ASR_API_TOKEN=change-me \
  -v asr-tasks-data:/data \
  ghcr.io/hafbit/asr-tasks:latest
```

首次启动会下载模型到 `/data/models`，时间取决于网络。API 存活检查为 `GET /health/live`，模型和 Worker 就绪检查为 `GET /health/ready`，交互式文档位于 `/docs`。

完全离线部署使用已经内置固定版本模型的完整镜像：

```bash
docker pull ghcr.io/hafbit/asr-tasks-full:latest
docker run --rm --platform linux/amd64 --network none \
  -p 8000:8000 \
  -e ASR_API_TOKEN=change-me \
  -v asr-tasks-data:/data \
  ghcr.io/hafbit/asr-tasks-full:latest
```

两个镜像使用独立名称。`latest` 分支发布 `:latest`；Git 标签 `v0.1.0` 发布镜像标签 `:0.1.0`。完整镜像内置 Paraformer `v2.0.5`、FSMN-VAD `v2.0.4` 和 CT-PUNC `v2.0.4`，模型位于 `/opt/asr-models`，不会被 `/data` 卷覆盖。

也可以使用 Compose：

```bash
ASR_API_TOKEN=change-me docker compose up

# 改用完整镜像
ASR_API_TOKEN=change-me \
  ASR_IMAGE=ghcr.io/hafbit/asr-tasks-full:latest \
  docker compose up
```

## 调用示例

上传资源：

```bash
curl -H 'Authorization: Bearer change-me' \
  -F file=@video.mp4 \
  http://localhost:8000/v1/assets
```

创建任务：

```bash
curl -H 'Authorization: Bearer change-me' \
  -H 'Content-Type: application/json' \
  -d '{"asset_id":"<asset-id>","hotwords":["量子计算","深度学习"]}' \
  http://localhost:8000/v1/transcription-jobs
```

也可以直接提交允许的 HTTP(S) 地址：

```bash
curl -H 'Authorization: Bearer change-me' \
  -H 'Content-Type: application/json' \
  -d '{"source_url":"https://example.com/video.mp4"}' \
  http://localhost:8000/v1/transcription-jobs
```

查询任务与结果：

```bash
curl -H 'Authorization: Bearer change-me' \
  http://localhost:8000/v1/transcription-jobs/<job-id>

curl -H 'Authorization: Bearer change-me' \
  http://localhost:8000/v1/transcription-jobs/<job-id>/result
```

URL 下载默认阻止环回、内网和云元数据地址。可信私有对象存储需要通过 `ASR_SOURCE_URL_ALLOWED_HOSTS`（逗号分隔）显式允许。

## 运行模式

```bash
asr-tasks all      # 默认：API 和 Worker 在同一容器
asr-tasks api      # 仅 API
asr-tasks worker   # 仅 Worker
asr-tasks migrate  # 仅执行数据库迁移
```

拆分模式下，API 与 Worker 必须挂载同一个 `/data` 卷。重要配置：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ASR_API_TOKEN` | 无 | Bearer Token；除非显式允许匿名，否则必须设置 |
| `ASR_ALLOW_UNAUTHENTICATED` | `false` | 仅用于可信开发环境 |
| `ASR_DATA_DIR` | `/data` | 数据、数据库、模型和结果根目录 |
| `ASR_WORKER_COUNT` | `1` | `all` 模式 Worker 数，最大 4 |
| `ASR_CPU_THREADS` | `min(物理核, 8)` | 每个 Worker 的模型 CPU 线程数 |
| `ASR_SOURCE_URL_ALLOWED_HOSTS` | 空 | 允许访问私网地址的可信主机名 |
| `ASR_FUZZY_HOTWORD_THRESHOLD` | `90` | 拼音模糊术语纠错阈值 |
| `ASR_OFFLINE_MODE` | `false` | 禁止模型联网回退；完整镜像固定为 `true` |

完整配置可在 `/docs` 和 `src/asr_tasks/config.py` 中查看。

## 本地开发

```bash
uv sync --extra dev
ASR_DATA_DIR=.data uv run alembic upgrade head
ASR_ALLOW_UNAUTHENTICATED=true uv run asr-tasks api
uv run pytest
uv run ruff check .
```

本地构建两个目标：

```bash
docker buildx build --platform linux/amd64 --target runtime --load -t asr-tasks:runtime .
docker buildx build --platform linux/amd64 --target full --load -t asr-tasks:full .
```

真实 FunASR 慢速测试默认跳过，需要额外安装模型依赖并设置 `ASR_RUN_MODEL_TESTS=1`。
