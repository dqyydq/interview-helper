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
