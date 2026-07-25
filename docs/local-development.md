# 本地开发与测试

本文说明 Interview Helper 第一阶段的本地开发拓扑、常用命令和故障定位方式。所有 Python 环境与依赖统一由 uv 管理。

## 进程拓扑

| 进程 | 默认地址 | 职责 |
| --- | --- | --- |
| React/Vite | `http://localhost:5173` | 产品界面 |
| FastAPI | `http://localhost:8000` | REST、SSE 与 WebSocket |
| PostgreSQL | `127.0.0.1:5432` | 持久化事实源（pgvector PostgreSQL 17 镜像） |
| Worker | 无端口 | 计划、简历、摘要与评估任务 |
| Mock Provider | `http://127.0.0.1:8010` | 本地确定性开发演示 |

## 初始化

```powershell
Copy-Item .env.example .env
docker compose up -d postgres

$env:UV_CACHE_DIR = "$PWD\.uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "$PWD\.uv-python"
uv venv --python 3.12 .venv
. .\.venv\Scripts\Activate.ps1
uv pip install -e ".\backend[dev]"

Set-Location backend
python -m alembic upgrade head
python -m app.cli.seed_companies
Set-Location ..\frontend
npm ci
```

`seed_companies` 可以重复运行：它导入公司与轮次骨架，不补造未经来源验证的公司风格事实。

## Docker-only 本地 AI 底座

本地 ASR 与 embedding 的运行时将只通过 Docker 提供，宿主机不安装 FunASR、TEI、PyTorch 或模型运行时。当前提交建立了受控 named volumes、禁用默认启动的 `model-loader` profile 与离线可验证的权重交付器；本地 FunASR / TEI 推理服务仍未接入。

普通的 `docker compose up -d postgres` 不会下载模型。面试进行中也不会下载模型、预热模型或重建向量索引。`postgres` 现使用不可变的 pgvector 0.8.1 / PostgreSQL 17 镜像，并只绑定到 `127.0.0.1`；已有 `interview-helper-postgres` volume 的安全升级与备份步骤见 [Docker-only 本地 AI](local-ai.md)。升级时绝不能使用 `docker compose down -v`。

只检查 Compose 配置时可以运行：

```powershell
docker compose config --quiet
docker compose --profile model-loader config --quiet
```

这两个命令不会拉取镜像、构建镜像、启动容器或下载模型。

## 启动

每个后端终端都必须先激活根目录中的 uv 环境。

```powershell
. .\.venv\Scripts\Activate.ps1
Set-Location backend
python -m uvicorn app.main:app --reload --port 8000 --ws-max-size 65536
```

```powershell
. .\.venv\Scripts\Activate.ps1
Set-Location backend
python -m app.workers.run
```

```powershell
. .\.venv\Scripts\Activate.ps1
Set-Location backend
python -m app.dev.mock_provider
```

```powershell
Set-Location frontend
npm run dev
```

## 数据库迁移

```powershell
. .\.venv\Scripts\Activate.ps1
Set-Location backend
python -m alembic current
python -m alembic upgrade head
python -m alembic check
```

模型变更后才能创建新迁移。提交前必须人工检查生成的列类型、索引、外键、nullable 和 downgrade：

```powershell
python -m alembic revision --autogenerate -m "describe change"
```

测试使用 `INTERVIEW_HELPER_TEST_DATABASE_URL`，不能指向开发数据库。测试会在会话开始前把测试数据库迁移到 head。

## 测试矩阵

后端：

```powershell
. .\.venv\Scripts\Activate.ps1
Set-Location backend
ruff format --check .
ruff check --no-cache app tests
python -m alembic check
python -m pytest -p no:cacheprovider
```

前端：

```powershell
Set-Location frontend
npm run lint
npm test
npm run build
npm run test:e2e
```

核心浏览器 E2E 使用 HTTP、SSE 和 WebSocket 测试替身，从公司选择走到可追溯评估报告，不依赖付费模型。后端集成测试会启动 `app.dev.mock_provider`，通过真实 PostgreSQL、OpenAI-compatible 网络适配器、WebSocket 与 worker 验证同一条核心链路。

## 常见问题

### API 显示数据库不可用

```powershell
docker compose ps
docker compose logs postgres
```

确认 `.env` 中开发和测试 URL 分别指向 `interview_helper` 与 `interview_helper_test`。

### 计划或报告一直排队

确认 `python -m app.workers.run` 正在运行，再打开“设置 → 系统诊断”查看 queued、running 和疑似停滞任务数量。诊断包只含状态与计数。

### 模型连接测试失败

确认 Base URL 已包含 Provider 的版本前缀，例如本地模拟 Provider 使用 `http://127.0.0.1:8010/v1`。后端会在其后追加 `/chat/completions` 或 `/audio/transcriptions`。

### 简历解析失败

支持 PDF、DOCX、Markdown 和 UTF-8 TXT，默认最大 5 MB。解析有硬超时并会有限重试；源文件缺失、签名不符或空文本会返回稳定错误码。

## 日志与诊断

每个 HTTP 请求都带 `X-Request-ID`。结构化日志只记录方法、路径、状态码、耗时以及明确绑定的 request/session/job ID，不记录请求正文。不要把模型密钥、简历或候选人回答手动写入日志字段。

“设置 → 系统诊断”展示数据库、worker、模型路由和隔离上传目录的状态。复制诊断包时，先检查 `privacy` 中四个布尔字段均符合预期。

## 实时部署边界

第一阶段以单机单用户、**单个 API 进程**为支持边界。`--ws-max-size 65536` 让 ASGI 服务器在应用读取前拒绝超过 64 KiB 的 WebSocket 帧；应用内还有同样的协议检查。单会话的“正在生成回答”锁目前位于 API 进程内，因此不要用多个 Uvicorn worker 或多个 API 实例服务同一数据库。横向扩展前，需要把该锁和连接广播替换为共享协调层。
