# 题目发现与可追溯导入实施计划

> 依据：[题目发现与来源可追溯导入设计规格](../specs/2026-07-23-question-discovery-design.md)
> 状态：用户已授权进入开发
> 原则：按安全边界和可验证闭环递进；每个里程碑可独立测试、提交和回滚

## 1. 本次交付的定义

本计划交付一个本地优先的“发现题目”闭环：

1. 用户配置一个独立的 Tavily 搜索连接器，不与 LLM 模型连接混用。
2. 用户以公司、轮次、岗位、技能、题型、难度和关键词检索公开资料，或粘贴至多 5 个链接。
3. 后端只通过受信的 Extract 连接器处理用户链接，形成受限来源卡片，不直接抓取任意 URL。
4. 用户显式绑定 RESEARCHER 模型后，系统在严格 token 预算中整理可审阅的题目候选；未绑定时安全降级为来源卡片和手动加题入口。
5. 用户批量编辑候选后，原子导入到指定题库为 LINK_IMPORT + DRAFT 题目，并保留最小来源追溯。
6. 仅 ACTIVE 题进入 Planner；已导入题在计划中保留真实 source_type 和 question_id。
7. 前端提供独立发现页、连接器设置页、来源证据与草稿审核深链。

本期不交付公共题库、站点爬虫、自动持续抓取、直接网页抓取、自动激活、自动修改风格包或变体导入。

## 2. 实施约束

- Python 依赖和测试均使用根目录 uv 环境：先执行 .venv\Scripts\Activate.ps1，再执行 Python 命令；新增依赖只能用 uv pip install。
- 所有文件改动使用 apply_patch；不覆盖用户已有改动。
- 题目发现使用 REST 创建、查询、取消和短轮询；不为它新增 SSE 或 WebSocket。
- 外部 URL、搜索结果和模型输出均视为不可信数据。应用服务器不得对用户 URL 发起 HTTP 请求。
- 搜索服务密钥、模型密钥、网页全文和完整模型提示词不得出现在 public schema、日志、BackgroundJob payload 或错误文本中。
- 发现功能所有行和查询均按 profile_id 隔离；不得用裸 UUID 绕过归属校验。
- 每个里程碑先补相关失败测试，再写最小实现，再运行对应测试、lint/build 和迁移校验。

## 3. 里程碑与依赖

~~~mermaid
flowchart LR
    M0["M0：契约与迁移"] --> M1["M1：安全连接器"]
    M1 --> M2["M2：发现运行与来源卡"]
    M2 --> M3["M3：Researcher 整理"]
    M3 --> M4["M4：导入、追溯与 Planner"]
    M4 --> M5["M5：前端发现与设置"]
    M5 --> M6["M6：清理、E2E 与发布检查"]
~~~

M0–M4 先完成后端完整契约，M5 只消费已锁定的 API；这样前端不会猜测未稳定字段，也不会把安全判断移到浏览器。

## 4. Milestone 0：领域契约、数据库与配置

### 4.1 枚举、模型与迁移

文件：

- 修改 backend/app/db/models/common.py
- 新增 backend/app/db/models/discovery.py
- 修改 backend/app/db/models/__init__.py
- 新增 backend/alembic/versions/0005_question_discovery.py
- 修改 backend/tests/models/test_common.py
- 修改 backend/tests/models/test_database_schema.py

动作：

1. 新增稳定枚举：DiscoveryProviderType（首期仅 tavily）、DiscoverySourceMode、DiscoveryRunStatus、DiscoverySourceStatus、DiscoveryCandidateStatus、DiscoveryImportStatus，以及 JobType.QUESTION_DISCOVERY。
2. 建立 DiscoveryConnector、QuestionDiscoveryRun、QuestionDiscoverySource、QuestionDiscoveryCandidate、QuestionDiscoveryCandidateEvidence、QuestionDiscoveryImport 和 QuestionSourceProvenance。
3. 每张业务表都包含 profile_id；run、source、candidate、evidence、import、provenance 的外键和复合约束必须表达同 profile / 同 run 的业务边界，服务层再做一次校验。
4. 对 QuestionDiscoveryImport 建立 profile + candidate + bank 的成功导入唯一性，并保存 candidate_content_hash、request hash、idempotency key 和候选 revision。
5. 对清理路径建立 profile/status/expires_at 等索引；临时 run/source/candidate/evidence 的删除与 Import/Provenance 历史引用使用 SET NULL；Question 仍按既有 archive / PlanQuestion RESTRICT 约束处理。
6. 手写 Alembic upgrade / downgrade，避免依赖 autogenerate 的外键推断。

验收：

- 空数据库可 upgrade 到 head、downgrade 一步、再 upgrade 并通过 alembic check。
- 所有新表进入 SQLModel metadata；关键 unique、FK、索引和删除动作有 schema 测试。
- 新增记录无法跨 profile 或跨 run 建立候选证据关联。

### 4.2 配置与统一错误码

文件：

- 修改 backend/app/core/config.py
- 修改 backend/app/api/errors.py
- 新增 backend/tests/discovery/test_limits.py

动作：

1. 固化规格中的硬上限：20 搜索结果、12 来源、5 链接、1 MiB 单连接器响应、16 KiB 单来源文本、1,200 / 8,000 CJK 摘录、6,000 Researcher 输入 tokens、2,048 输出 tokens、20 候选、15 秒单请求、60 秒单运行、每 profile 4 并发、30 天保留。
2. 部署配置只能把上限调低；服务层再次强制 cap，不能仅相信环境变量。
3. 定义内容安全的错误码，例如 discovery_connector_unavailable、discovery_url_blocked、discovery_budget_exceeded、discovery_cancelled、discovery_import_conflict。

验收：

- 配置边界测试覆盖最小/最大值。
- 任何错误响应都不含 URL 正文、密钥、供应商原始响应或模型提示词。

## 5. Milestone 1：安全搜索连接器与 URL 策略

### 5.1 Provider 契约与 Tavily 适配器

文件：

- 新增 backend/app/discovery/providers/__init__.py
- 新增 backend/app/discovery/providers/base.py
- 新增 backend/app/discovery/providers/tavily.py
- 新增 backend/tests/discovery/test_tavily_provider.py

动作：

1. 定义 SearchProvider 的 search、extract、health_check 和 capability flags；URL 导入要求 safe_extract 契约。
2. Tavily 端点由服务端固定，用户只能提供 API key 与显示名称，不能设置 endpoint、代理或回调地址。
3. HTTP 客户端使用 trust_env=False、15 秒超时、流式响应和 1 MiB 读取上限；将认证、限流、超时、5xx 映射为稳定错误码。
4. 仅公开受限来源 DTO：标题、规范化 URL、域、分类、短摘录、抓取时间和安全能力；永不公开或持久化整段供应商原始响应。

验收：

- Injectable HTTP transport 覆盖 Search / Extract 请求格式、401/403/429/5xx、超时、超大响应与响应脱敏。
- 没有 safe_extract 能力的 provider 不能用于链接导入。

### 5.2 URL 策略与独立连接器配置

文件：

- 新增 backend/app/discovery/url_policy.py
- 新增 backend/app/services/discovery_connectors.py
- 新增 backend/app/schemas/discovery.py
- 新增 backend/app/api/routes/question_discoveries.py
- 修改 backend/app/api/routes/__init__.py
- 新增 backend/tests/discovery/test_url_policy.py
- 新增 backend/tests/api/test_discovery_connectors.py

动作：

1. URL policy 接受 HTTPS；仅本地显式开发开关允许 HTTP。拒绝 userinfo、非默认端口、localhost、IP literal、单标签主机和非公网解析结果。
2. 用 IDNA 规范化、去尾点、exact 或点边界子域匹配实现 allow / deny；deny 始终优先。
3. 注入 DNS resolver，任一 A / AAAA 记录非 global 即拒绝；明确这只是输入筛查，第三方 Extract 的重定向与网络隔离是已声明的受信 provider 边界。
4. DiscoveryConnector 使用现有 SecretCipher 加密 API key。删除连接器先清除密钥再软删除；public schema 只暴露 has_api_key、健康状态和能力。
5. 提供连接器 list/create/update/delete/test API，并全部 profile scoped。

验收：

- 测试 IPv4/IPv6、私网/保留地址、IDN、DNS 多记录、localhost、端口、userinfo 与 allow/deny 边界。
- 测试密钥从不出现在 GET、错误、日志 mock 或删除后的记录中。

## 6. Milestone 2：发现运行、后台任务与来源卡闭环

### 6.1 运行服务与 worker

文件：

- 新增 backend/app/services/question_discovery.py
- 新增 backend/app/workers/discovery_jobs.py
- 修改 backend/app/workers/run.py
- 新增 backend/tests/discovery/test_discovery_service.py
- 新增 backend/tests/discovery/test_discovery_worker.py
- 新增 backend/tests/api/test_question_discoveries.py

动作：

1. POST discovery 创建 QUEUED run 和 BackgroundJob；job payload 只保存 run UUID，内部 idempotency key 带 profile 和 run，避免 BackgroundJob 的全局唯一键冲突。
2. Worker 使用既有 skip_locked 模式 claim job，按 SEARCHING / EXTRACTING 阶段获取来源并写入受限 Source 记录。
3. 终态固定为 SUCCEEDED、PARTIAL、NO_RESULTS、FAILED、CANCELLED；RUNNING 只是过程状态，PARTIAL 永不自动改写为成功。
4. 取消 API 只允许 QUEUED/RUNNING 到 CANCEL_REQUESTED 的条件更新。worker 在每个外部调用前、每批写入前和终态提交前检查取消状态。
5. 没有 Researcher 绑定时先完成“安全来源卡片 + 手动新增入口”的闭环；不会生成伪候选。
6. 实现 run 列表、详情、sources、cancel、delete。运行删除只允许终态 / 已取消状态，且不影响已导入题。

验收：

- 搜索、粘贴链接、部分来源失败、无结果、取消竞争、超时和并发上限均有服务/worker/API 测试。
- 已取消 run 不再启动新外部请求或写入新候选。
- API 查询、取消、删除、source/candidate 访问均拒绝跨 profile 或跨 run UUID。

## 7. Milestone 3：Researcher 整理与模型预算

文件：

- 修改 backend/app/services/model_connections.py
- 新增 backend/app/agents/question_researcher.py
- 新增 backend/app/services/question_curation.py
- 新增 backend/tests/discovery/test_curation.py
- 修改 backend/tests/api/test_model_connections.py

动作：

1. 增加 exact-only Researcher resolver；发现流程禁止使用现有 RESEARCHER 到 INTERVIEWER 回退。
2. 在调用前确定性裁剪来源：每源最多 1,200 CJK 等价字符、总计最多 8,000；再把 6,000 token 硬 cap 与实际 effective_input_budget 取小。
3. 复用 structured_request_budget_validator 和 StructuredOutputRunner(max_repairs=1)。首次调用和 schema 修复调用都再次校验 token；修复不能绕过预算。
4. 将网页摘录作为不可信 user JSON 输入；Researcher 固定 schema 只产生最多 20 个候选，每个候选必须有同 run、同 profile 的 evidence。
5. 无绑定、模型错误、schema 无法修复或预算超限时，保留来源卡并写可解释状态，而不是补全或幻觉题目。

验收：

- Researcher 未绑定时绝不向 Interviewer 连接发送来源内容。
- 注入提示、超预算输入、超预算 schema repair、无证据候选、伪 source_id 与跨 run evidence 都被拒绝。
- 模型外发 payload 仅含显式检索条件、受限摘录和最小去重摘要。

## 8. Milestone 4：去重、原子导入、来源追溯与 Planner

### 8.1 导入与题库来源

文件：

- 修改 backend/app/services/questions.py
- 修改 backend/app/api/routes/questions.py
- 修改 backend/app/schemas/question.py
- 修改 backend/app/services/question_discovery.py
- 扩展 backend/tests/api/test_questions.py
- 扩展 backend/tests/discovery/test_discovery_service.py

动作：

1. 实现精确题干 hash 与有限相似题提示。第一阶段不写入 QuestionVariant。
2. POST /question-discoveries/{id}/imports 接收 bank_id、items、candidate_revision 和 Idempotency-Key；同 key + 同请求哈希重放原结果，不同 hash 返回 409。
3. 批量导入先校验全部 item；任意 item 失效、跨 profile、revision 过期或重复时不写任何新题。通过后一个事务写 Question(DRAFT, LINK_IMPORT)、Import 与 immutable Provenance。
4. 增加 provenance list/delete，普通 QuestionCreate 不获得设置 source_type 的能力。
5. 题目 archive 与 provenance 删除遵循规格：archive 不物理删 Question；单独删除 provenance 不影响历史 PlanQuestion 的 prompt_snapshot。

验收：

- 并发重复导入、同 key 重试、不同 body 冲突、批次回滚、跨题库/跨 profile 失败都有测试。
- 导入结果为 DRAFT + LINK_IMPORT，且来源链接/摘录可查、可单独删除。

### 8.2 Planner 兼容性

文件：

- 修改 backend/app/services/question_retrieval.py
- 修改 backend/app/services/interview_planning.py
- 如有需要修改 backend/app/schemas/interview_plan.py
- 扩展 backend/tests/services/test_question_retrieval.py
- 扩展 backend/tests/api/test_interview_plans.py

动作：

1. PlanCandidate 读取真实 Question.source_type，不再把持久化题目硬编码成 MANUAL。
2. 所有已入库 Question（含 LINK_IMPORT）都写入 PlanQuestion.question_id；非 Question 来源才为 null。
3. 用 canonical Company.slug 和 company_slug:round_key 对 ACTIVE 题做 exact / generic / mismatch 排序加权；generic 题保持可选，错配题降权而非静默删除。
4. 调整 source weight / choice 逻辑，使 LINK_IMPORT 不会因来源真实化而被排除。

验收：

- DRAFT 题仍被候选池排除。
- ACTIVE LINK_IMPORT 题保留 source_type 和 question_id，并按公司/轮次匹配参与排序。
- 现有 MANUAL、RESUME、GENERATED 计划测试不回归。

## 9. Milestone 5：前端发现、设置与来源追溯

### 9.1 路由、类型与 API

文件：

- 修改 frontend/src/app/router.tsx
- 修改 frontend/src/app/shell/CommandPalette.tsx
- 新增 frontend/src/features/discovery/types.ts
- 新增 frontend/src/features/discovery/api.ts
- 新增 frontend/src/features/discovery/QuestionDiscoveryPage.tsx
- 新增 frontend/src/features/discovery/DiscoveryForm.tsx
- 新增 frontend/src/features/discovery/CandidateList.tsx
- 新增 frontend/src/features/discovery/SourceEvidenceDrawer.tsx
- 新增 frontend/src/features/discovery/ImportCandidatesDialog.tsx

动作：

1. 新增 /questions/discover，不将“发现”塞入 KnowledgeBasePage 的本地 state tab。
2. 使用 React Query 短轮询 1.5–2 秒查询运行状态；终态停止轮询，不新增 SSE/WS。
3. 表单只提交显式发现字段和 1–5 URL；浏览器绝不抓取、判断 URL 安全或保存网页全文。
4. 候选卡显示结构化建议、来源类别、证据、低置信度和重复提示；导入抽屉维持一把 Idempotency-Key，用于提交/显式重试。
5. 完成导入后失效题库与发现查询，并跳至 /questions?bank_id=…&status=draft。

### 9.2 连接器设置与题库衔接

文件：

- 新增 frontend/src/features/settings/discovery/DiscoverySettingsPage.tsx
- 新增 frontend/src/features/settings/discovery/api.ts
- 新增 frontend/src/features/settings/discovery/types.ts
- 修改 frontend/src/features/settings/SettingsTabs.tsx
- 修改 frontend/src/features/knowledge/KnowledgeBasePage.tsx
- 修改 frontend/src/features/knowledge/api.ts
- 修改 frontend/src/features/knowledge/types.ts
- 修改 frontend/src/styles.css

动作：

1. 新增 /settings/discovery，支持连接器新增、测试、健康状态、密钥更新与删除；只显示 has_api_key 与安全能力。
2. 设置页显示 RESEARCHER 绑定状态和隐私说明，但不显示任何密钥或完整供应商错误。
3. 知识库将“发现题目”作为路由 Link；保留手动加题；对 LINK_IMPORT 题提供来源面板和删除 provenance。
4. 样式以 discovery-*、connector-*、source-provenance-* 命名空间扩展现有 Precision Console，补充 768px / 390px 的无横向溢出规则。

验收：

- 无连接器、Researcher 未绑定、PARTIAL、NO_RESULTS、budget exceeded、取消、重复、幂等导入均有清晰可恢复 UI。
- 无配置时用户仍可手动加题；发现候选不会自动进入题库或 Planner。

## 10. Milestone 6：测试、清理、可观测性与发布校验

文件：

- 新增或扩展 backend/tests/discovery/
- 新增或扩展 backend/tests/api/test_question_discoveries.py
- 新增 frontend/src/features/discovery/QuestionDiscoveryPage.test.tsx
- 新增 frontend/src/features/settings/discovery/DiscoverySettingsPage.test.tsx
- 修改 frontend/src/features/knowledge/KnowledgeBasePage.test.tsx
- 修改 frontend/src/app/router.test.tsx
- 修改 frontend/src/app/shell/CommandPalette.test.tsx
- 新增 frontend/e2e/question-discovery.spec.ts
- 修改 README.md、.env.example、docs/architecture.md（如该文件存在）

动作：

1. 加入 30 天临时发现数据清理任务或可测试的 service/CLI，由 worker 定期执行；不会删除已导入题目的独立 provenance。
2. 记录不含正文、密钥或用户输入的连接器延迟/失败、run 结果、候选/导入/激活计数、Researcher token 和清理指标。
3. 完成 API、数据库、worker、前端单测和 Playwright E2E；覆盖桌面与窄屏无横向溢出。
4. 更新本地部署说明，包括 Tavily key、模型 RESEARCHER 显式绑定、隐私边界和第三方资料免责声明。

验收：

- 后端全量 pytest、前端 lint/test/build、Playwright E2E、Alembic upgrade/downgrade/check 全部通过。
- pip-audit 与 npm audit 无高危依赖问题；没有真实 key、完整网页正文或用户数据进入仓库。

## 11. 建议提交边界

1. feat: add discovery domain schema and limits
2. feat: add secured discovery connector and URL policy
3. feat: add discovery runs and source retrieval worker
4. feat: curate discovery candidates with strict researcher budget
5. feat: import sourced questions and preserve planner provenance
6. feat: add question discovery and connector settings UI
7. test: harden discovery lifecycle and end-to-end coverage

每次提交前运行受影响测试；Milestone 4 与 Milestone 5 前分别运行完整后端和完整前端验证，避免 Planner 与 UI 在最后才暴露契约不一致。
