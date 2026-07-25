# 百炼 Qwen3-ASR-Flash 直连转写设计

> 产品：Interview Helper  
> 日期：2026-07-25  
> 状态：已确认，待规格复核后实施  
> 范围：为现有“语音转写”角色增加阿里云百炼 `qwen3-asr-flash` 专用连接器。

## 1. 目标与边界

用户在模拟面试中完成一段浏览器录音后，应用将录音二进制在内存中编码为 Base64，并通过百炼 OpenAI-compatible Chat Completions 接口调用 `qwen3-asr-flash`，直接取得可编辑的文字结果。

本期不使用 OSS、不创建异步转写任务、不轮询、不使用 `fun-asr` 的 `Transcription.async_call` 路径。这样保持当前“录音 → 转写 → 用户确认/修改 → 填入回答”的交互，避免临时音频对象存储和公开 URL 的隐私、部署与运维负担。

本期也不实现实时字幕、说话人分离、长音频文件转写、自动切换其他模型或语音合成；这些能力分别属于 `qwen3-asr-flash-realtime`、`fun-asr` / `qwen3-asr-flash-filetrans` 或后续独立功能。

## 2. 已确认决策

1. 新增独立连接类型 `dashscope_qwen_asr`，仅用于 `transcriber` 角色；它不能绑定面试官、评估官、规划、记忆、研究、教练或向量检索角色。
2. 现有 `openai_compatible` 与 `anthropic` 聊天连接不可绑定为 `transcriber`。这会阻止 DeepSeek 等纯文本模型出现在语音转写下拉框中。
3. 初始模型固定为 `qwen3-asr-flash`，不把任意聊天模型名当作语音模型接受。
4. 默认中国大陆（北京）兼容模式基址为 `https://dashscope.aliyuncs.com/compatible-mode/v1`；用户可在连接中填写其 Workspace 专属基址 `https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`。
5. 单段音频硬上限为 10 MiB、5 分钟。这与 `qwen3-asr-flash` 文件识别限制一致；浏览器端与后端都必须在向百炼发送前阻止超限音频。
6. 转写音频、Base64 内容、原始百炼响应和 API Key 都不得写入数据库、日志、后台任务 payload、诊断快照或浏览器错误信息。
7. 一个 profile 只能显式绑定一个转写连接；任何失败都保留文字回答路径，不做静默模型或 Key 回退。

百炼官方将 `qwen3-asr-flash` 定义为支持 URL 或 Base64 音频的 OpenAI-compatible HTTP 模型，支持 `webm`，限制为不超过 10 MB、5 分钟：
[Qwen-ASR API](https://help.aliyun.com/en/model-studio/qwen-asr-api-reference)、[音频规格](https://help.aliyun.com/zh/model-studio/asr-model)。

## 3. 后端契约

### 3.1 连接与角色校验

`ProviderType` 增加 `DASHSCOPE_QWEN_ASR = "dashscope_qwen_asr"`。现有 `model_connections.provider_type` 为字符串列，因此不需要为了该枚举本身增加迁移；必须补齐模型、schema、服务和 API 测试，防止值在各层不一致。

连接保存的公开字段仅包括显示名称、类型、兼容模式 Base URL、模型名、启用状态、测试/健康状态和 `has_api_key`。百炼 Key 仍使用既有 `SecretCipher` 加密保存。模型创建和更新时，`dashscope_qwen_asr` 的模型名必须为 `qwen3-asr-flash`，Base URL 必须是 HTTPS 兼容模式地址。

`bind_role` 在服务端按能力校验，而不是仅依赖前端筛选：

- `transcriber` 只接受 `dashscope_qwen_asr`；
- `dashscope_qwen_asr` 只接受 `transcriber`；
- 原有聊天角色维持现有 OpenAI-compatible / Anthropic 行为。

### 3.2 转写请求与响应

保留 `POST /api/transcriptions` 的同步 API 和既有 `SpeechToTextProvider` 抽象。新增 `DashScopeQwenAsrProvider`：

1. 读取经过大小、MIME 与非空校验的音频 bytes；
2. 在内存中生成 Base64，不落盘；
3. 向 `{base_url}/chat/completions` 发出非流式 JSON 请求：

```json
{
  "model": "qwen3-asr-flash",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "input_audio",
          "input_audio": { "data": "<base64-audio>" }
        }
      ]
    }
  ],
  "stream": false,
  "asr_options": { "language": "zh", "enable_itn": true }
}
```

4. 从 `choices[0].message.content` 提取非空文本，读取可选音频语言标注；
5. 转换为既有 `TranscriptionResult`，让前端继续显示可编辑确认框。

连接 API Key 以单个请求的 Bearer 认证传递，不使用 DashScope SDK 的进程级 `dashscope.api_key` 或 `base_http_api_url` 全局变量，避免多 profile / 多连接串号。适配器继续使用项目已有的 `httpx`，不为这一条 REST 调用新增 SDK 依赖。

### 3.3 错误、限制与健康

- 认证、授权、余额、429、超时、上游 5xx、无效模型、格式不支持和空结果都映射为稳定、无敏感内容的转写错误码。
- Base64 膨胀后请求会大于原始文件；10 MiB 是原始音频上限，服务端限制必须在编码前执行。
- 泛用“聊天连通性测试”不适用于该模型，因为它需要音频输入。Qwen ASR 连接创建后为“待首次转写验证”；首次成功转写才标记为健康。设置页不得把无音频的聊天健康测试伪装成模型可用性。
- 未绑定、连接禁用、缺少 Key 或转写失败时，现有文字输入与重新录音操作仍完整可用。

## 4. 前端体验

设置页的“模型与 Agent”区域增加 `百炼 Qwen ASR` 创建入口和表单说明：该连接只用于语音转文字；需要百炼 API Key、兼容模式 Base URL 与固定模型 `qwen3-asr-flash`。API Key 只在新增/替换时输入，列表永不回显。

“语音转写”角色下拉框只列出启用、具有 Key 的 Qwen ASR 连接。其它角色不显示该连接。普通聊天模型不再出现在“语音转写”候选中。

录音组件继续录制后转写，但在开始录音前提示“单段最多 5 分钟 / 10 MB”。若达到浏览器端大小或时长限制，停止录制、保留用户已输入的草稿，并展示“改用文字回答”的明确操作；不上传超限音频。

## 5. 测试与验收

后端：

- 适配器测试覆盖请求 URL、Bearer 认证、Base64 请求体、成功解析、空文本、4xx/429/5xx/超时和原始 10 MiB 限制；断言日志/异常不包含 Key 或 Base64。
- API 与服务测试覆盖连接 CRUD、加密凭据、仅可绑定 `transcriber`、普通聊天连接不可绑定转写、未绑定时文字兜底与上游失败映射。
- 既有 OpenAI-compatible 转写测试保持通过，避免因新类型破坏已有适配器。

前端：

- 设置页只在正确角色显示 Qwen ASR 连接，且普通连接不会误列入转写候选；
- 录音组件覆盖 10 MiB / 5 分钟限制、成功确认、失败重试及文字兜底；
- 全量 TypeScript、Vitest 与构建通过。

端到端验收：用户填入真实百炼 Key 并显式绑定 Qwen ASR 后，一段小于限制的 `webm` 录音能返回可编辑中文转写；取消或失败时仍能提交纯文字回答。 
