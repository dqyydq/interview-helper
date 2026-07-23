# 模型 Provider 与角色路由

Interview Helper 把模型协议与 Agent 角色分离。同一个连接可以绑定多个角色，也可以让面试官、评估官、Coach 和语音转写分别使用不同服务。

## 支持的协议

### OpenAI-compatible

聊天连接要求实现：

- `POST <base_url>/chat/completions`
- 普通 JSON 响应
- 面试官角色需要 SSE streaming，使用 `data: ...` 与 `data: [DONE]`
- 评估和 Coach 需要接受 `response_format.type=json_schema`

语音转写要求实现：

- `POST <base_url>/audio/transcriptions`
- multipart 文件上传
- 返回至少包含非空 `text` 的 JSON

### Anthropic-compatible

聊天连接使用 Anthropic Messages 风格接口。第一阶段不使用 Anthropic-compatible 连接执行 STT，因此“语音转写”下拉框只显示 OpenAI-compatible 连接。

## 角色

| 角色 | 是否必需 | 数据范围 |
| --- | --- | --- |
| interviewer | 是 | 公司/轮次风格、当前计划、最近对话、选中摘要和记忆 |
| evaluator | 是 | 冻结计划、按题确认回答及文本代码附件 |
| planner | 否 | 第一阶段确定性规划器可无模型运行 |
| context_summarizer | 否 | 待压缩的会话片段，不替代原始消息 |
| researcher | 否 | 第一阶段尚未自动联网研究 |
| coach | 否 | 已完成报告与用户选择的必要原回答 |
| embedding | 否 | 第一阶段可使用非向量回退检索 |
| transcriber | 否 | 用户主动录制并上传的单段音频 |

“面试官”和“评估官”必须绑定且连接健康，系统才标记核心链路就绪。可选角色未绑定时使用产品定义的回退策略或关闭对应能力。

## 添加真实连接

在“设置 → 模型连接”填写：

- 协议类型
- Base URL
- 模型 ID
- API Key
- 可选额外请求头
- context window 与最大输出 token
- provider capability 标记

保存后密钥和额外请求头会使用 `INTERVIEW_HELPER_ENCRYPTION_SECRET` 加密；列表和详情接口只返回 `has_api_key`，不会回传密钥。先执行连接测试，再进行角色绑定。

不要把浏览器可访问的前端地址作为 Base URL，也不要在 URL 查询参数中放密钥。

## 本地确定性模拟 Provider

```powershell
. .\.venv\Scripts\Activate.ps1
Set-Location backend
python -m app.dev.mock_provider
```

连接参数：

```text
Provider: OpenAI-compatible
Base URL: http://127.0.0.1:8010/v1
Model: mock-interview
API Key: local-mock-only
```

对 `127.0.0.1`、`::1` 和 `localhost` 的本地 Provider 连接会自动绕过环境代理；远程 Provider 仍遵循系统的代理配置。

它提供：

- `GET /health`
- 非流式健康检查
- 流式面试官追问
- 基于输入 ID 的 schema 合法评估结果
- Coach 结果
- 固定 STT 文本

它不会联网，不加载真实模型，不评判候选人，也不应用于基准测试或生产环境。

## 上下文与成本

模型连接的 context window 是预算上限的输入之一。运行时先保留系统规则、当前题目和最近确认回答，再按预算选择摘要、简历片段、题库证据和长期记忆。超预算内容被显式排除并记录计数，不静默截断事实源。

Provider 返回 usage 时系统保存真实 token；没有 token count 能力时使用带安全余量的保守估算。最终报告仍按题读取原始确认回答，不用实时摘要代替证据。
