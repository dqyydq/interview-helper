# Interview Helper Phase 1 实施计划

> 依据：[产品与设计规格](../specs/2026-07-18-interview-helper-product-design.md)
> 目标：交付可本地运行的单用户 MVP 闭环
> 状态：计划完成后等待用户授权实施
> 原则：每个里程碑独立可运行、可测试、可回滚

## 1. 交付目标

Phase 1 完成后，用户应能：

1. 在本地配置一个或多个 OpenAI-compatible / Anthropic-compatible 对话模型。
2. 管理公司、轮次、题库和简历。
3. 在 Precision Console 中选择公司与轮次，补充岗位、简历和题库上下文。
4. 生成可解释的面试计划。
5. 通过文字或语音转文字参加一次实时模拟面试，并在代码白板中提交代码片段。
6. 结束后获得带真实回答证据的结构化报告，并进入文字复盘教练。
7. 在没有 Redis、pgvector、云账户或托管服务的情况下完成上述流程。

Phase 1 不实现公司联网研究、链接自动导题、公共风格仓库、多用户、TTS、代码执行和真实招聘预测。

## 2. 实施约束

- Python 环境使用 uv。运行 Python 前执行 `.venv\\Scripts\\activate`；安装使用 `uv pip install`。
- 当前 `.venv` 指向缺失的 uv Python，正式实施开始时先修复环境；不得假设它可直接运行。
- PostgreSQL 是唯一必需外部服务。
- REST、SSE、WebSocket 按设计规格分工，不为同一事件维护两套流式协议。
- 后端业务逻辑不得直接依赖具体模型 SDK；所有模型调用经过 provider 接口。
- 模型生成结果必须通过 Pydantic schema 校验后进入数据库。
- 外部输入（简历、题目、模型输出）全部视为不可信数据。
- Precision Console 以选定图为视觉基准，不在实施中重新设计。
- 每个任务先写失败测试，再写最小实现，再运行相关测试。

## 3. 推荐目录结构

```text
interview_helper/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── deps.py
│   │   │   ├── errors.py
│   │   │   └── routes/
│   │   ├── agents/
│   │   ├── core/
│   │   ├── db/
│   │   │   ├── models/
│   │   │   ├── base.py
│   │   │   └── session.py
│   │   ├── context/
│   │   ├── memory/
│   │   ├── providers/
│   │   ├── realtime/
│   │   ├── schemas/
│   │   ├── services/
│   │   └── workers/
│   ├── alembic/
│   ├── tests/
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   ├── components/
│   │   ├── features/
│   │   ├── lib/
│   │   ├── pages/
│   │   ├── styles/
│   │   └── test/
│   ├── package.json
│   └── vite.config.ts
├── seed/
│   ├── companies/
│   └── role-matrices/
├── docs/
├── docker-compose.yml
├── .env.example
├── .gitignore
└── README.md
```

## 4. 里程碑与任务顺序

## Milestone 0：项目基础与可重复开发环境

### Task 0.1：修复 Python 环境并建立后端包

**文件**

- `backend/pyproject.toml`
- `backend/app/__init__.py`
- `backend/app/main.py`
- `backend/tests/test_health.py`

**动作**

1. 检查当前 `.venv` 是否含用户手工文件；确认后使用 uv 重建损坏环境。
2. 激活 `.venv\\Scripts\\activate`。
3. 使用 `uv pip install` 安装 FastAPI、Uvicorn、SQLModel/SQLAlchemy、Alembic、asyncpg、Pydantic Settings、HTTPX、python-multipart、sse-starlette、structlog、pytest、pytest-asyncio。
4. 创建 FastAPI app factory、`/api/health` 和统一应用生命周期。
5. 测试健康检查返回稳定 schema：`status`、`version`、`database`。

**验收**

- 后端可启动。
- `pytest backend/tests/test_health.py` 通过。
- 数据库不可用时 health 返回 degraded，而不是进程崩溃。

### Task 0.2：建立前端工程

**文件**

- `frontend/package.json`
- `frontend/vite.config.ts`
- `frontend/src/main.tsx`
- `frontend/src/app/router.tsx`
- `frontend/src/test/setup.ts`

**动作**

1. 创建 Vite + React + TypeScript 项目。
2. 安装 React Router、TanStack Query、Zustand、Zod、Lucide React、Vitest、Testing Library。
3. 建立基础路由页面：`/interviews`、`/questions`、`/reports`、`/settings`，每个页面提供明确的未配置状态和返回路径。
4. 建立 API client，统一 base URL、错误解码和请求 ID。

**验收**

- `npm run build` 通过。
- 路由 smoke test 通过。
- 404 页面不泄露内部错误。

### Task 0.3：PostgreSQL 与本地编排

**文件**

- `docker-compose.yml`
- `.env.example`
- `.gitignore`
- `backend/app/core/config.py`
- `backend/app/db/session.py`

**动作**

1. Docker Compose 只启动 PostgreSQL；不加入 Redis。
2. 定义开发、测试数据库连接配置。
3. 配置 async engine、session factory、连接池和事务依赖。
4. `.env.example` 只提供变量名和安全示例，不包含真实 key。

**验收**

- 新环境可按 README 启动 PostgreSQL。
- 后端可以建立连接并在关闭时释放连接池。
- `.env`、上传文件和本地密钥目录均被忽略。

**建议提交**：`chore: scaffold local development foundation`

## Milestone 1：领域模型、迁移与错误契约

### Task 1.1：创建基础枚举与公共字段

**文件**

- `backend/app/db/models/common.py`
- `backend/app/schemas/common.py`
- `backend/tests/models/test_common.py`

**动作**

- 定义 UUID、UTC 时间戳、软删除、版本号字段。
- 定义稳定枚举：题目状态、会话状态、任务状态、评估锚点、消息角色、可见性。
- 枚举在 API 中输出字符串，不暴露数据库实现。

### Task 1.2：创建内容与面试实体

**文件**

- `backend/app/db/models/company.py`
- `backend/app/db/models/question.py`
- `backend/app/db/models/resume.py`
- `backend/app/db/models/interview.py`
- `backend/app/db/models/context.py`
- `backend/app/db/models/memory.py`
- `backend/app/db/models/evaluation.py`
- `backend/app/db/models/model_connection.py`
- `backend/app/db/models/job.py`
- `backend/alembic/versions/0001_initial_schema.py`
- `backend/tests/models/`

**动作**

- 实现设计规格第 14 节的实体与外键。
- 对历史引用内容采用版本化或软删除。
- `InterviewMessage` 使用 session 内单调递增 `sequence` 唯一约束。
- `ConversationSegment`、`ContextSummary` 和 `ContextSnapshot` 保留消息范围与版本。
- `MemoryItem` 通过 `MemorySource` 引用原始 session/message，并支持状态、冲突和过期。
- `BackgroundJob` 使用幂等键唯一约束。
- `EvidenceItem` 保存来源元数据和字段级关联，不保存整页复制内容。

**验收**

- 空数据库可升级到 head，也可降级一次。
- 关键唯一约束和级联规则有数据库测试。
- 删除题目不会破坏历史计划；删除简历允许清除原文但保留已生成报告的最小引用。

### Task 1.3：统一错误响应

**文件**

- `backend/app/api/errors.py`
- `backend/app/api/middleware.py`
- `backend/tests/api/test_errors.py`
- `frontend/src/lib/api/errors.ts`

**动作**

- 错误 schema：`code`、`message`、`request_id`、`field_errors`、`retryable`。
- 将验证、数据库冲突、provider 失败和未知异常映射为稳定错误码。
- 日志记录内部堆栈，前端不显示堆栈。

**建议提交**：`feat: add versioned domain schema and error contract`

## Milestone 2：模型连接与 Provider 抽象

### Task 2.1：定义 provider 契约

**文件**

- `backend/app/providers/base.py`
- `backend/app/providers/types.py`
- `backend/tests/providers/test_contract.py`

**动作**

- 定义 `chat`、`stream_chat`、`health_check` 与可选 `embeddings`。
- 内部消息包含 system/user/assistant/tool，但 provider 自行转换协议。
- 流事件统一为 `text_delta`、`tool_call`、`usage`、`completed`、`failed`。
- 定义 `TokenCounter` 能力：官方计数、本地 tokenizer 或带安全余量的保守估算。
- 对结构化输出实现统一校验与有限次数修复入口。

### Task 2.2：OpenAI-compatible 适配器

**文件**

- `backend/app/providers/openai_compatible.py`
- `backend/tests/providers/test_openai_compatible.py`

**动作**

- 支持自定义 base URL、API key、模型名、超时和额外请求头。
- 使用 HTTPX 或官方 SDK 的异步接口，但不得让 SDK 类型越过 provider 边界。
- 使用模拟 HTTP 服务测试请求格式、流式解析、超时和错误脱敏。

### Task 2.3：Anthropic-compatible 适配器

**文件**

- `backend/app/providers/anthropic_compatible.py`
- `backend/tests/providers/test_anthropic_compatible.py`

**动作**

- 正确转换 system、content blocks 和流事件。
- 测试 tool use、文本流、限流与认证错误。
- 不假设 Anthropic 适配器提供 STT 或 embeddings。

### Task 2.4：模型配置与角色绑定 API

**文件**

- `backend/app/api/routes/model_connections.py`
- `backend/app/services/model_connections.py`
- `frontend/src/features/settings/models/`

**动作**

- CRUD 模型连接，密钥写入前加密。
- 读取 API 永不返回完整密钥。
- 提供连接测试和角色绑定。
- 保存 context window、最大输出 token、tokenizer 类型、prompt cache 和 token count 能力。
- 至少要求 Interviewer 与 Evaluator 有有效绑定；其他角色可回退到默认对话模型。
- `context_summarizer` 未绑定时回退到 Planner。

**验收**

- 两类协议均通过契约测试。
- 错误日志不包含 API key。
- 前端可创建两个 provider 实例并分别绑定角色。

**建议提交**：`feat: add multi-provider model connections`

## Milestone 3：公司、题库与简历上下文

### Task 3.1：公司和轮次 CRUD

**文件**

- `backend/app/schemas/company.py`
- `backend/app/services/companies.py`
- `backend/app/api/routes/companies.py`
- `backend/tests/api/test_companies.py`
- `frontend/src/features/companies/`

**动作**

- 创建、编辑、归档公司和轮次。
- 轮次顺序由显式 position 控制，不写死一二三面。
- 风格字段支持 field confidence 与来源引用。
- 未提供证据的公司显示“自定义草案”，不伪装成公共结论。

### Task 3.2：首批公司内容种子

**文件**

- `seed/companies/alibaba.yaml`
- `seed/companies/bytedance.yaml`
- `seed/companies/baidu.yaml`
- `seed/companies/tencent.yaml`
- `seed/companies/meituan.yaml`
- `seed/companies/xiaohongshu.yaml`
- `backend/app/services/seeding.py`
- `backend/tests/services/test_seeding.py`

**动作**

- 公司名称和轮次骨架可直接种子化。
- 所有具体风格结论必须在单独的内容研究任务中附来源后写入；实施者不得凭印象补写。
- 种子脚本幂等，更新通过版本字段完成。

### Task 3.3：题库与题目管理

**文件**

- `backend/app/schemas/question.py`
- `backend/app/services/questions.py`
- `backend/app/api/routes/question_banks.py`
- `backend/app/api/routes/questions.py`
- `backend/tests/api/test_questions.py`
- `frontend/src/features/questions/`

**动作**

- 实现集合、题目、标签、状态、来源和变体。
- 保存规范化文本哈希并阻止集合内精确重复。
- 支持筛选、排序、批量归档和分页。
- MVP 只提供手动新增与编辑，不出现不可用的 URL 导入入口。

### Task 3.4：简历上传和解析任务

**文件**

- `backend/app/services/uploads.py`
- `backend/app/services/resume_parser.py`
- `backend/app/api/routes/resumes.py`
- `backend/app/workers/job_runner.py`
- `backend/app/workers/handlers/resume_parse.py`
- `backend/tests/services/test_resume_parser.py`
- `frontend/src/features/resumes/`

**动作**

- 校验 PDF、DOCX、Markdown、TXT 的 MIME、扩展名、大小和解析时限。
- 抽取纯文本后由 Planner 模型生成结构化 sections/claims。
- 解析作为 `BackgroundJob`，通过 PostgreSQL worker 执行。
- 提供 SSE 进度与失败重试。
- 原始文件存本地受控目录，不将路径直接返回前端。

**验收**

- 手动题目可以完整 CRUD。
- 同一简历重复提交不会创建重复解析任务。
- 恶意扩展名、超大文件和空文档被明确拒绝。
- SSE 断开后可使用 Last-Event-ID 继续接收状态。

**建议提交**：`feat: add interview context management`

## Milestone 4：面试计划与可解释选题

### Task 4.1：岗位能力矩阵

**文件**

- `seed/role-matrices/llm-application-engineer.yaml`
- `backend/app/services/role_matrix.py`
- `backend/tests/services/test_role_matrix.py`

**动作**

- 定义 LLM 应用开发的能力维度、题型与默认权重。
- 公司轮次只覆盖相关字段，不复制整份矩阵。
- schema 保持专业可扩展，后续可增加后端、前端、产品等矩阵。

### Task 4.2：确定性候选池

**文件**

- `backend/app/services/question_retrieval.py`
- `backend/tests/services/test_question_retrieval.py`

**动作**

- 从用户题库、简历 claims 和场景模板构建候选池。
- 先用数据库过滤与权重规则生成候选，再让模型做编排；模型不得访问全部数据库。
- 实现近期使用降权、同能力重复限制和时间预算。

### Task 4.3：Planner Agent 与 InterviewPlan

**文件**

- `backend/app/agents/planner.py`
- `backend/app/schemas/interview_plan.py`
- `backend/app/services/interview_planning.py`
- `backend/app/api/routes/interview_plans.py`
- `backend/tests/agents/test_planner.py`

**动作**

- 输入候选池、轮次风格、岗位矩阵、简历摘要和时长。
- 输出有序题目、每题时间、来源、选中原因、追问预算和能力覆盖。
- 校验总时间、题量、来源 ID、重复和能力覆盖；不合法时修复或失败。
- 计划生成通过 PostgreSQL job + SSE 返回进度。

**验收**

- 固定候选池和模拟模型响应可重复产生合法计划。
- 每道题都能定位来源。
- 无题库时仍可用简历与内置场景模板生成最小计划。

**建议提交**：`feat: add explainable interview planning`

## Milestone 5：Precision Console 与面试准备页

### Task 5.1：设计令牌与应用壳

**文件**

- `frontend/src/styles/tokens.css`
- `frontend/src/styles/global.css`
- `frontend/src/app/AppShell.tsx`
- `frontend/src/components/navigation/TopBar.tsx`
- `frontend/src/components/navigation/CommandPalette.tsx`
- `frontend/src/components/navigation/BottomStatusBar.tsx`
- `frontend/src/components/navigation/*.test.tsx`

**动作**

- 把设计规格的 OKLCH 色彩、字体、4px spacing、规则线、时长和焦点令牌写入 `tokens.css`。
- 实现 72px 深色顶栏、命令搜索、主导航和焦点管理。
- 使用 Lucide 图标；公司标记使用单色抽象符号或文字，不复制品牌 Logo。
- 支持 reduced motion 和键盘导航。

### Task 5.2：三栏 Precision Console

**文件**

- `frontend/src/pages/InterviewConsolePage.tsx`
- `frontend/src/features/interview-console/CompanyIndex.tsx`
- `frontend/src/features/interview-console/RoundRail.tsx`
- `frontend/src/features/interview-console/RoundBrief.tsx`
- `frontend/src/features/interview-console/InterviewerPreview.tsx`
- `frontend/src/features/interview-console/InterviewActionBar.tsx`
- `frontend/src/features/interview-console/interview-console.css`
- `frontend/src/features/interview-console/*.test.tsx`

**动作**

- 精确实现 22/54/24 三栏比例和底部固定行动区。
- 公司选择联动轮次、简报、预览和底栏摘要。
- 不完整配置时就地说明并禁用开始按钮。
- 选择状态同时使用颜色、规则线和文本。
- 加载时使用与布局同构的骨架，不显示卡片瀑布。

### Task 5.3：面试准备页

**文件**

- `frontend/src/pages/InterviewSetupPage.tsx`
- `frontend/src/features/interview-setup/`

**动作**

- 确认岗位、时长、简历、题库、输入方式和模型绑定。
- 生成计划后展示题目来源分布和能力覆盖，但不泄露参考答案。
- 用户确认后创建 session。

### Task 5.4：视觉基线测试

**文件**

- `frontend/e2e/interview-console.spec.ts`
- `frontend/e2e/screenshots/`

**动作**

- 以选定参考图作为 1440×1024 的人工对照。
- Playwright 捕获 1440、1024、768、390px。
- 检查三栏、底栏、溢出、文字截断、焦点环和最小点击目标。
- 不以单张截图作为完成标准；需逐项核对结构和交互。

**建议提交**：`feat: implement precision console selection flow`

## Milestone 6：上下文管理与结构化记忆

详细设计以 [上下文管理与记忆系统设计规格](../specs/2026-07-18-context-memory-design.md) 为准。

### Task 6.1：TokenCounter 与 TokenBudget

**文件**

- `backend/app/context/token_counter.py`
- `backend/app/context/token_budget.py`
- `backend/tests/context/test_token_counter.py`
- `backend/tests/context/test_token_budget.py`

**动作**

- 统一官方 token count、本地 tokenizer 和保守估算三种计数方式。
- 计算 context window 减去输出预留、协议开销和安全余量后的有效输入预算。
- 实现 60/75/85/95% 压缩级别及各层最小保障。
- 未知 tokenizer 至少使用 15% 安全余量，绝不把未知计数当作零。

**验收**

- 多种 context window 下均不生成超预算上下文。
- system、当前问题、最近完整回答和未解决追问无法被低优先级内容挤出。

### Task 6.2：会话状态、分段与结构化摘要

**文件**

- `backend/app/context/session_state.py`
- `backend/app/context/segmentation.py`
- `backend/app/context/summarizer.py`
- `backend/app/schemas/context.py`
- `backend/tests/context/test_segmentation.py`
- `backend/tests/context/test_summarizer.py`

**动作**

- 为每个完整题目建立 `ConversationSegment`，当前题目关闭前不压缩。
- Orchestrator 确定性维护 `InterviewContextState`；模型只能建议，不能直接改状态。
- Context Summarizer 输出核心回答、显式事实、权衡、边界、未解决点、附件和 evidence message IDs。
- 校验摘要证据范围；失败时保留原文并标记 `summary_failed`。
- 非常长会话允许合并旧摘要，但保留子摘要 ID 和覆盖范围。

### Task 6.3：ContextBuilder 与 ContextSnapshot

**文件**

- `backend/app/context/builder.py`
- `backend/app/context/retrieval.py`
- `backend/app/context/snapshot.py`
- `backend/tests/context/test_builder.py`
- `backend/tests/context/test_snapshot.py`

**动作**

- ContextBuilder 成为唯一 provider prompt 组装入口。
- 按安全规则、角色/风格、计划/状态、近期原文、摘要、检索内容、输出 schema 的顺序组装。
- 按稳定 ID 去重，按优先级和 token 预算裁剪。
- 每次重要调用保存纳入/排除 ID、各层 token、压缩级别、计数方法和 provider usage。
- 默认不保存完整 prompt 文本；调试捕获必须显式开启并脱敏。

**验收**

- 属性测试证明任何消息序列都不会丢失当前问题。
- 同一状态与预算产生稳定、可解释的 snapshot。
- 切换不同 context window 模型无需改写原始会话。

### Task 6.4：长期记忆生命周期与检索

**文件**

- `backend/app/memory/types.py`
- `backend/app/memory/writer.py`
- `backend/app/memory/conflicts.py`
- `backend/app/memory/retriever.py`
- `backend/tests/memory/test_lifecycle.py`
- `backend/tests/memory/test_retrieval.py`

**动作**

- 实现 proposed/active/conflicted/rejected/expired 生命周期；`pinned` 作为独立优先级属性。
- 用户明确项目事实和偏好可以激活；能力与薄弱点首次只 proposed，至少两场独立证据后再自动 active。
- 相同 canonical key 的不同值新增版本并进入冲突处理，不原位覆盖。
- MVP 使用 JSONB、标签和 PostgreSQL 全文检索；pgvector 不进入必需依赖。
- 检索综合相关性、固定、显式事实、置信度、近期性、公司/岗位和重复使用惩罚。
- Interviewer 不读取历史评级数字；Evaluator 不用长期记忆改变本场评级。

### Task 6.5：记忆 API 与用户控制

**文件**

- `backend/app/api/routes/memories.py`
- `backend/app/services/memories.py`
- `frontend/src/pages/MemorySettingsPage.tsx`
- `frontend/src/features/memory/MemoryList.tsx`
- `frontend/src/features/memory/MemoryPreview.tsx`
- `frontend/src/features/memory/*.test.tsx`

**动作**

- 提供列表、编辑、确认、固定、拒绝、删除和按场遗忘。
- 面试准备页展示本场将使用的记忆，可临时排除。
- 关闭跨场记忆后停止提取和检索，但不静默删除已有数据。
- 按场遗忘时移除该来源并重算多来源记忆，防止误删其他会话证据。

### Task 6.6：上下文诊断与长会话验收

**文件**

- `backend/app/api/routes/context_diagnostics.py`
- `frontend/src/features/diagnostics/ContextUsage.tsx`
- `backend/tests/context/test_long_session.py`
- `frontend/e2e/long-interview-memory.spec.ts`

**动作**

- 记录每次调用的 token 分层、压缩率、检索数量和估算方法，不记录敏感正文。
- 模拟 60 分钟会话和小上下文模型，强制触发多次压缩。
- 验证第二场面试只召回已激活结构化记忆，删除后不再召回。
- 验证摘要失败时仍可使用原文继续，最终评估证据不受摘要影响。

**建议提交**：`feat: add layered context and user-controlled memory`

## Milestone 7：实时面试引擎

### Task 7.1：会话服务与状态机

**文件**

- `backend/app/services/interview_sessions.py`
- `backend/app/realtime/state_machine.py`
- `backend/tests/realtime/test_state_machine.py`

**动作**

- 只允许设计规格定义的状态迁移。
- 进入 interviewing 前冻结 InterviewPlan 版本。
- finish 幂等；重复请求不重复创建评估任务。

### Task 7.2：WebSocket 协议

**文件**

- `backend/app/realtime/events.py`
- `backend/app/realtime/connection_manager.py`
- `backend/app/api/routes/interview_live.py`
- `backend/tests/realtime/test_websocket_protocol.py`
- `frontend/src/lib/realtime/interviewSocket.ts`

**动作**

- 实现设计规格第 15.3 节事件。
- 所有提交使用 event ID 与 sequence 去重。
- 支持最后确认序号恢复与缺失事件补发。
- 对消息大小、空闲时间和无效状态做限制。

### Task 7.3：Interviewer Agent

**文件**

- `backend/app/agents/interviewer.py`
- `backend/app/services/interview_orchestrator.py`
- `backend/tests/agents/test_interviewer.py`

**动作**

- 构建分层上下文，不把参考答案传给 Interviewer。
- 约束一次一个主问题、追问预算、时间预算和自然结束。
- 将 provider stream 映射为 WebSocket `assistant.delta/message`。
- 保存最终消息，不把每个 token 写入数据库。

### Task 7.4：实时面试前端

**文件**

- `frontend/src/pages/LiveInterviewPage.tsx`
- `frontend/src/features/live-interview/Transcript.tsx`
- `frontend/src/features/live-interview/AnswerComposer.tsx`
- `frontend/src/features/live-interview/SessionTimer.tsx`
- `frontend/src/features/live-interview/ConnectionState.tsx`
- `frontend/src/features/live-interview/*.test.tsx`

**动作**

- 展示问题、用户确认文本、Agent 流式文字、剩余时间和连接状态。
- 面试中不显示评分。
- 断线时禁用重复提交并自动恢复。
- 用户可暂停、重述和提前结束。

**验收**

- 模拟断网后恢复，不重复也不丢失已确认消息。
- 同一 answer event 重放只入库一次。
- provider 超时不会让 session 永久卡在 interviewing。

**建议提交**：`feat: add resumable realtime interview sessions`

## Milestone 8：评估、报告与复盘教练

### Task 8.1：结构化评估 schema

**文件**

- `backend/app/schemas/evaluation.py`
- `backend/tests/schemas/test_evaluation.py`

**动作**

- 实现四档锚点、逐题评价、能力维度、证据、缺口、动作和置信度。
- 证据只能引用现有 message ID。
- 缺证据时强制输出 `evidence_insufficient`，不能给强结论。

### Task 8.2：Evaluator Agent

**文件**

- `backend/app/agents/evaluator.py`
- `backend/app/services/evaluation.py`
- `backend/app/workers/handlers/evaluate_interview.py`
- `backend/tests/agents/test_evaluator.py`

**动作**

- 评估任务读取冻结计划、风格包版本和完整消息。
- Evaluator 按 `PlanQuestion` 切分并读取对应原始回答，先做逐题评价，再聚合为本场能力结论。
- 实时摘要只用于恢复对话连续性，不能替代原始回答成为评分证据。
- 长期记忆仅用于报告中的跨场趋势对比，不得改变本场评分。
- 使用结构化输出；校验引用、维度完整性和枚举。
- 有限修复失败后保留会话并标记可重评。
- SSE 返回阶段进度，不流出内部推理文本。

### Task 8.3：报告页面

**文件**

- `frontend/src/pages/InterviewReportPage.tsx`
- `frontend/src/features/reports/`
- `frontend/src/features/reports/*.test.tsx`

**动作**

- 实现概览、逐题时间线、能力矩阵、证据抽屉、改进动作与练习计划。
- 点击证据定位对应消息。
- 少于两次可比会话时隐藏趋势，不显示空图表。
- 不显示 Offer 概率。

### Task 8.4：Coach Agent

**文件**

- `backend/app/agents/coach.py`
- `backend/app/api/routes/report_coach.py`
- `backend/tests/agents/test_coach.py`
- `frontend/src/features/reports/CoachPanel.tsx`

**动作**

- Coach 只访问报告与必要消息片段。
- 支持解释评级、示范重答和生成专项练习。
- 明确区分“用户原回答”和“建议答案”。

**建议提交**：`feat: add evidence-based evaluation and coaching`

## Milestone 9：语音输入与代码白板

### Task 9.1：STT 抽象与音频上传

**文件**

- `backend/app/providers/speech_base.py`
- `backend/app/providers/openai_transcription.py`
- `backend/app/api/routes/transcriptions.py`
- `backend/tests/providers/test_transcription.py`
- `frontend/src/features/live-interview/VoiceRecorder.tsx`

**动作**

- 浏览器 MediaRecorder 录制分段音频。
- 首个适配器调用可配置的 OpenAI-compatible transcription endpoint。
- Anthropic 对话连接不被错误用作 STT。
- 转写结果先让用户确认，再作为 answer commit。
- STT 不可用时文字输入完整可用。

### Task 9.2：代码白板

**文件**

- `frontend/src/features/live-interview/CodeWhiteboard.tsx`
- `frontend/src/features/live-interview/codeWhiteboard.test.tsx`
- `backend/app/schemas/attachments.py`

**动作**

- 使用 CodeMirror，支持语言选择、复制、清空和附加到回答。
- 服务端只保存文本、语言和大小，不执行代码。
- 限制附件大小，防止把超大内容塞入模型上下文。

**建议提交**：`feat: add confirmed speech input and code whiteboard`

## Milestone 10：安全、可观测性与发布

### Task 10.1：安全加固

**文件**

- `backend/app/core/security.py`
- `backend/app/core/redaction.py`
- `backend/tests/security/`

**动作**

- 模型密钥加密与日志脱敏。
- 上传目录隔离、文件名随机化、解析超时和删除接口。
- 限制 WebSocket 消息大小、连接频率和未确认消息数量。
- 对简历、题目和模型内容明确数据边界，防止覆盖系统规则。

### Task 10.2：日志与诊断

**文件**

- `backend/app/core/logging.py`
- `backend/app/api/routes/diagnostics.py`
- `frontend/src/pages/DiagnosticsPage.tsx`

**动作**

- 结构化日志包含 request/session/job ID，但不含答案全文和密钥。
- 本地诊断展示数据库、worker、模型连接和文件目录状态。
- 提供用户可复制的脱敏诊断包。

### Task 10.3：端到端与发布文档

**文件**

- `frontend/e2e/full-interview.spec.ts`
- `README.md`
- `docs/local-development.md`
- `docs/model-providers.md`
- `docs/privacy.md`

**动作**

- 使用模拟 provider 完成从选择公司到报告的 E2E。
- README 包含 uv 环境、PostgreSQL、前后端启动和模型配置。
- 说明本地数据位置、删除方式和备份边界。
- 记录已知限制：无 TTS、无代码执行、无联网公司研究。

**验收**

- 新机器按 README 可以启动并完成模拟-provider 演示。
- 后端单元/集成测试、前端单元测试、构建和核心 E2E 全部通过。
- 选定 Precision Console 在 1440×1024 下通过视觉人工核对。

**建议提交**：`chore: harden and document local MVP`

## 5. 测试执行矩阵

| 变更类型 | 必跑测试 |
| --- | --- |
| 数据模型/迁移 | model tests + Alembic upgrade/downgrade |
| Provider | provider contract + adapter mock HTTP tests |
| REST API | route tests + service tests |
| SSE/worker | job idempotency + reconnect tests |
| WebSocket | protocol + sequence + reconnect tests |
| 上下文/记忆 | token budget + compaction invariants + retrieval/deletion tests |
| Planner/Evaluator | schema + fixture transcript tests |
| UI 组件 | Vitest + Testing Library |
| Precision Console | component tests + 4 viewport screenshots |
| 完整流程 | Playwright E2E with mock provider |

## 6. 持续集成门槛

每个合并请求至少执行：

1. Python 格式、静态检查和测试。
2. Alembic 从空库升级到 head。
3. 前端 lint、类型检查、单元测试和 build。
4. 核心 E2E 的模拟 provider 流程。
5. 依赖和密钥扫描。

不允许通过降低测试覆盖、跳过 schema 校验或硬编码模型响应来修复 CI。

## 7. 风险与缓解

### 模型输出不稳定

- 使用结构化 schema、有限修复、fixture 测试和可切换模型重试。

### 实时连接复杂

- 先实现纯文字 WebSocket，再加入 STT；所有事件有序号与幂等 ID。

### 公司风格容易变成伪事实

- Phase 1 只种子化骨架；具体风格内容必须有来源任务和置信度。

### 题库、简历和风格同时进入上下文导致超限

- ContextBuilder 统一预算和裁剪；60/75/85/95% 分级压缩，当前题目、近期原文和未解决追问不可静默丢失。

### 摘要漂移或长期记忆造成错误先验

- 完整 transcript 保持事实源；摘要必须引用范围内证据，记忆可追溯且可冲突暂停；Evaluator 仍按题读取原文评分。

### UI 因信息多退化成卡片墙

- 组件验收以连续三栏、细规则线和底部行动区为硬约束；视觉回归之外保留人工核对清单。

### 本地密钥和隐私数据泄露

- 后端密钥存储、日志脱敏、上传隔离、显式删除和脱敏诊断包。

## 8. 实施起点

获得用户授权后，从 Milestone 0 / Task 0.1 开始，不并行跨越领域边界。第一个可展示检查点是：

- PostgreSQL 可启动。
- FastAPI health 可用。
- React 应用壳可打开。
- Precision Console 使用静态真实形态数据渲染，但开始按钮明确标记为尚未接入，直到 Milestone 5 完成真实准备流程。

每完成一个 Milestone，先交付运行结果、测试结果和剩余风险，再开始下一个 Milestone。
