# Interview Helper

一个面向技术候选人的开源 AI 模拟面试应用。产品根据目标公司、面试轮次、岗位方向、个人题库和简历，组织连续追问，并在结束后提供基于真实回答证据的评估。

当前项目已进入 Phase 1 实施阶段。Milestone 0 提供 FastAPI 健康检查、React 应用壳与本地 PostgreSQL 编排。

## 文档

- [产品与设计规格](docs/superpowers/specs/2026-07-18-interview-helper-product-design.md)
- [上下文管理与记忆系统设计](docs/superpowers/specs/2026-07-18-context-memory-design.md)
- [Phase 1 实施计划](docs/superpowers/plans/2026-07-18-interview-helper-phase1-implementation-plan.md)

## 已确认技术方向

- React + TypeScript
- Python + FastAPI
- PostgreSQL
- REST + SSE + WebSocket
- OpenAI-compatible 与 Anthropic-compatible 模型协议
- uv 管理 Python 环境

## 开发状态

### 1. 准备配置

```powershell
Copy-Item .env.example .env
```

### 2. 启动 PostgreSQL

```powershell
docker compose up -d postgres
```

### 3. 安装并启动后端

Python 环境统一使用 uv 管理：

```powershell
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "$PWD\.uv-python"
uv venv --python 3.12 .venv
. .\.venv\Scripts\Activate.ps1
uv pip install -e ".\backend[dev]"
Set-Location backend
python -m uvicorn app.main:app --reload
```

健康检查地址为 `http://localhost:8000/api/health`。PostgreSQL 不可用时进程仍能启动，并返回 `degraded`。

数据库结构由 Alembic 管理：

```powershell
Set-Location backend
python -m alembic upgrade head
python -m alembic current
```

Docker 首次初始化会同时创建 `interview_helper` 与 `interview_helper_test`。pytest 只连接测试数据库，并在测试开始前自动升级迁移。

### 模型连接

打开 `http://localhost:5173/settings` 可添加 OpenAI-compatible 或 Anthropic-compatible 模型连接，并分别绑定面试官、评估官等 Agent 角色。API Key 与额外请求头使用 `INTERVIEW_HELPER_ENCRYPTION_SECRET` 在本地后端加密，读取接口不会返回密钥；正式使用前请在 `.env` 中替换示例加密密钥。

### 公司骨架与后台任务

首次迁移后可安全地导入六家公司轮次骨架。骨架不包含未经验证的公司风格结论，重复执行不会产生重复版本：

```powershell
Set-Location backend
python -m app.cli.seed_companies
```

简历上传后会写入 PostgreSQL 任务队列。开发环境需另开一个终端启动 worker；任务进度由 API 通过 SSE 提供：

```powershell
. .\.venv\Scripts\Activate.ps1
Set-Location backend
python -m app.workers.run
```

支持 PDF、DOCX、Markdown 与 TXT，默认上限为 5 MB。上传文件保存在 `data/uploads`，该目录不会进入 Git。

### 4. 安装并启动前端

```powershell
Set-Location frontend
npm install
npm run dev
```

前端默认运行在 `http://localhost:5173`。

### 5. 验证

```powershell
. .\.venv\Scripts\Activate.ps1
python -m pytest backend\tests
Set-Location frontend
npm test
npm run build
```
