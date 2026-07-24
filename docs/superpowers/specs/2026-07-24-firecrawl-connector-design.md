# Firecrawl 题目发现连接器补充规格

> 产品：Interview Helper
> 状态：已确认并进入实现
> 日期：2026-07-24
> 范围：在既有 Tavily 发现流程中增加 Firecrawl，不改变本地手动加题流程

## 已确认决策

1. Tavily 与 Firecrawl 都是用户自己配置的发现连接器；每个 profile 的每种服务最多保存 3 个连接器/API Key。
2. 禁用的连接器仍计入 3 个名额；删除连接器会清除本地加密凭据并释放名额。
3. 每次发现任务必须由用户明确选择一个连接器。系统不会在额度不足、限流或失败时自动换用其他 Key 或其他服务。
4. Firecrawl 同时支持公开网页搜索和用户粘贴链接的正文提取，输出仍要经过既有 URL 策略、来源卡片、Researcher 审核与人工导入流程。
5. 所有 Key 只在本地后端加密存储；不支持自定义 endpoint、代理、请求头或回调地址。

## 服务端契约

- 固定 Firecrawl v2 服务地址 `https://api.firecrawl.dev/v2`，认证使用 `Authorization: Bearer <API_KEY>`。
- 搜索使用 `POST /search` 的 web source；先得到元数据 URL，再交由既有 URLPolicy 校验。
- 提取使用 `POST /scrape`，按已校验 URL 逐条请求 Markdown。Firecrawl 返回的最终 URL 仍须再次通过 URLPolicy 校验，才能成为来源证据。
- 搜索结果不直接写入题库，不自动激活，也不会影响简历、记忆、风格包或历史评估。
- 连接器创建使用 PostgreSQL transaction advisory lock，将同一 profile + provider 的计数和写入串行化，避免并发请求绕过 3 个上限。

## 用户界面

- “设置 → 题目发现”按服务显示 Tavily 与 Firecrawl 连接器，并分别展示 `n / 3`。
- 新建表单可以选择服务；编辑已有连接器时服务类型不可更改。
- 达到该服务上限时，只禁用这一类的新增操作，另一类仍可继续新增。
- “发现题目”页的连接器下拉框初始为空，必须由用户选择已启用且有凭据的连接器；选项会显示服务类型。

## 验收

- Tavily 和 Firecrawl 可以各自保存 3 个 Key，第四个同类请求稳定返回 `discovery_connector_provider_limit`。
- 并发创建同一种服务时最多成功 3 个。
- Firecrawl 的搜索、逐 URL 提取、认证/额度/限流错误和响应大小上限均有适配器测试。
- Firecrawl 运行记录保留被选择的 provider，来源归因可区分 Tavily 与 Firecrawl。
- 前端不能自动选择第一个连接器，也不能向不可用连接器发起任务。
