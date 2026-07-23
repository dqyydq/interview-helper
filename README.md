# Interview Helper

Interview Helper 是一个本地优先、开源的 AI 模拟面试应用。它根据目标公司、面试轮次、岗位方向、个人题库和简历组织连续追问，并在结束后生成可回到原始回答的证据化评估。

第一阶段聚焦大模型应用开发岗位，内置阿里巴巴、字节跳动、百度、腾讯、美团和小红书的**轮次骨架**。公司风格不是官方结论：只有用户录入且带来源的材料才会形成风格依据；也可以自行添加任意公司。

## 第一阶段能力

- 公司与一面、二面、三面等轮次选择，自定义公司骨架。
- 手工题库、标签、归档与确定性选题。
- PDF、DOCX、Markdown、TXT 简历上传、异步解析和显式删除。
- OpenAI-compatible 与 Anthropic-compatible 多模型连接及 Agent 角色路由。
- WebSocket 实时面试、断线续传、暂停、重述和提前结束。
- 浏览器语音录制、OpenAI-compatible STT、转写确认后提交。
- 只作为文本证据保存的代码白板，不执行代码。
- 分层上下文预算、可追溯摘要、跨场长期记忆和 token 诊断。
- 按题原始证据评估、能力维度、专项练习与 Coach 复盘。
- 结构化脱敏日志、本地系统诊断和可复制诊断包。

## 快速开始：确定性本地演示

### 1. 环境要求

- Git
- [uv](https://docs.astral.sh/uv/) 与 Python 3.12
- Node.js 20.19+ 或 22.12+ 及更高版本，以及 npm
- Docker Desktop（用于 PostgreSQL 17）

```powershell
git clone <your-repository-url> interview_helper
Set-Location interview_helper
Copy-Item .env.example .env
```

正式使用前，请把 `.env` 中的 `INTERVIEW_HELPER_ENCRYPTION_SECRET` 改成仅在本机保存的随机值。已有模型密钥写入数据库后再修改该值，会导致旧密钥无法解密。

### 2. 启动 PostgreSQL

```powershell
docker compose up -d postgres
docker compose ps
```

首次启动会创建 `interview_helper` 与 `interview_helper_test` 两个数据库。

### 3. 创建 uv 环境并安装后端

```powershell
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "$PWD\.uv-python"
uv venv --python 3.12 .venv
. .\.venv\Scripts\Activate.ps1
uv pip install -e ".\backend[dev]"
Set-Location backend
python -m alembic upgrade head
python -m app.cli.seed_companies
Set-Location ..
```

后续运行任何 Python 命令前都先激活同一个 uv 环境：

```powershell
. .\.venv\Scripts\Activate.ps1
```

### 4. 启动四个本地进程

分别打开四个 PowerShell 终端。

后端 API：

```powershell
Set-Location interview_helper
. .\.venv\Scripts\Activate.ps1
Set-Location backend
python -m uvicorn app.main:app --reload --port 8000 --ws-max-size 65536
```

后台任务 worker：

```powershell
Set-Location interview_helper
. .\.venv\Scripts\Activate.ps1
Set-Location backend
python -m app.workers.run
```

确定性模拟 Provider：

```powershell
Set-Location interview_helper
. .\.venv\Scripts\Activate.ps1
Set-Location backend
python -m app.dev.mock_provider
```

前端：

```powershell
Set-Location interview_helper\frontend
npm ci
npm run dev
```

打开 `http://localhost:5173`。后端健康检查位于 `http://localhost:8000/api/health`，模拟 Provider 健康检查位于 `http://127.0.0.1:8010/health`。

### 5. 配置模拟 Provider

进入“设置 → 模型连接”，新建一个连接：

| 字段 | 值 |
| --- | --- |
| 协议 | OpenAI-compatible |
| 连接名称 | Local deterministic mock |
| Base URL | `http://127.0.0.1:8010/v1` |
| 模型名称 | `mock-interview` |
| API Key | `local-mock-only` |

测试连接后，把它至少绑定到“面试官”和“评估官”；为了体验完整功能，还可以绑定“训练教练”和“语音转写”。随后在“模拟面试”中选择公司与轮次、生成计划、完成作答并进入评估报告。

模拟 Provider 的回答和报告是固定规则生成的，只用于验证产品链路，不代表真实模型质量或候选人水平。

## 验证

PostgreSQL 必须处于运行状态：

```powershell
Set-Location interview_helper
. .\.venv\Scripts\Activate.ps1
Set-Location backend
ruff format --check .
ruff check --no-cache app tests
python -m alembic upgrade head
python -m alembic check
python -m pytest -p no:cacheprovider

Set-Location ..\frontend
npm run lint
npm test
npm run build
npm run test:e2e
```

## 数据与隐私

- PostgreSQL 数据保存在 Docker volume `interview-helper-postgres`。
- 简历源文件默认位于 `data/uploads/<profile-id>/`，使用随机文件名且不会进入 Git。
- 模型密钥和额外请求头使用本地 `INTERVIEW_HELPER_ENCRYPTION_SECRET` 加密后存入 PostgreSQL。
- 模型服务会收到完成当前 Agent 工作所需的题目、简历片段、确认回答或评估上下文；STT 服务会收到用户主动确认上传的音频。
- 实时摘要和长期记忆是派生数据，完整原始消息仍是事实源；最终评估只引用已确认的本场原始回答。
- 系统日志和诊断包不记录回答全文、简历正文、模型密钥或本地绝对路径。

完整边界见 [隐私与本地数据](docs/privacy.md)。

## 当前限制

- 暂无面试官文字转语音（TTS），面试官以文字回答。
- 代码白板不执行代码，也不启动容器或访问网络。
- 暂无自动联网研究公司风格；公司材料由用户本地维护。
- 第一阶段无账户系统，默认面向单机单用户，不应直接暴露到公网。
- 实时面试以单个 API 进程为部署边界；同一会话的并发作答保护目前是进程内机制。启动命令必须保留 `--ws-max-size 65536`，横向扩展前需要引入共享会话锁与连接协调层。
- 内置公司只提供可编辑轮次骨架，不宣称代表官方招聘标准。

## 文档

- [本地开发与测试](docs/local-development.md)
- [模型 Provider 与角色路由](docs/model-providers.md)
- [隐私、删除与备份](docs/privacy.md)
- [产品与设计规格](docs/superpowers/specs/2026-07-18-interview-helper-product-design.md)
- [上下文与记忆设计](docs/superpowers/specs/2026-07-18-context-memory-design.md)
- [第一阶段实施计划](docs/superpowers/plans/2026-07-18-interview-helper-phase1-implementation-plan.md)

## 开源协议

本项目采用 [MIT License](LICENSE)。
