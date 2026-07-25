# Docker-only 本地 AI

> 状态：Docker 交付底座、受校验的模型 loader、离线 FunASR 与 TEI 运行 profile、应用内的本地能力探测和角色绑定，以及 pgvector 后台语义索引均已建立。

Interview Helper 的本地语音转写与本地 embedding 只会通过 Docker 容器运行。宿主机不需要、也不应为本应用安装 FunASR、TEI、PyTorch、CUDA Python 包或模型运行时。云端模型仍然是独立、显式选择的选项；本地服务故障时应用不得把音频、简历或回答静默发送到云端。

## 当前 Compose 行为

- PostgreSQL 使用不可变的 `pgvector/pgvector@sha256:3e8b3adfd27b5707128f60956f62a793c3c9326ea8cfaf0eab7adccb5d700b21` 镜像（pgvector 0.8.1 / PostgreSQL 17，多架构 OCI index），并保留原有 named volume 键 `interview-helper-postgres`。
- PostgreSQL 只映射到 `127.0.0.1:${POSTGRES_PORT:-5432}`，不会暴露到局域网或公网。
- `interview-helper-models` 保存将来已校验的模型文件，`interview-helper-model-state` 保存下载和校验状态；它们与业务数据库 volume 分离。
- `model-loader` 位于显式的 `model-loader` profile。普通 `docker compose up -d postgres` 不会启动它，也不会拉取镜像、构建镜像或下载模型。
- loader 只能安装受支持 preset：SenseVoiceSmall、multilingual-e5-small 和 BGE-M3。每个 preset 固定到不可变的 ModelScope commit；它会先检查模型 volume 的保守可用空间，再写入 staging 区、逐文件校验 SHA-256，成功后才原子写入 active marker；离线包还必须带匹配的 `offline-manifest.json`。同一 preset 的安装会通过共享 state volume 串行化，避免重复点击或两个容器竞争覆盖 active 状态。
- 本阶段已经提供本地 FunASR 与 TEI 的 Docker profile、设置页探测和 `Transcriber` / `Embedding` 的本地能力绑定。`0008` / `0009` Alembic migration 会启用 `vector` 扩展并创建隔离的向量表；不要手工执行 `CREATE EXTENSION vector`。

只检查 Compose 文件时使用：

```powershell
docker compose config --quiet
docker compose --profile model-loader config --quiet
```

以上检查不会启动或拉取容器。当前 loader 会先把模型下载或导入到 staging、校验清单和哈希，再原子地标记为已验证。面试过程中不会下载模型、预热模型、重嵌入或建立向量索引。

`model-loader` 的 `status` / `verify` 命令不会访问网络；`install` 是唯一会从 ModelScope 下载权重的操作。开发人员需要显式运行该命令才会构建 loader 镜像并进入下载流程。仅当模型权重已校验、但对应推理 profile 尚未启动时，不要把“权重已校验”误认为“本地转写或检索已经可用”。

如果主机被关闭或 loader 被强制终止，确认**没有同一 preset 的安装仍在运行**后，才可以显式清理其锁；该命令不会删除模型、active marker 或 PostgreSQL 数据：

```powershell
docker compose --profile model-loader run --rm --no-deps model-loader recover-lock --preset sensevoice-small --confirm-no-active-install
```

普通 `install` 永远不会自动抢占锁，因为低带宽下载大型模型可能持续很久；后续应用设置页会把 `install_in_progress` 作为“正在安装，请稍后刷新”的可恢复状态处理。

## 运行已校验的本地服务

本地服务只消费已经由 `model-loader` 完整校验并标记为 active 的固定版本模型。运行 profile 不会下载权重、不会调用 ModelScope，也不会在失败时把音频或文本自动转发到云端。

先选择一个预设并显式安装。以下命令是唯一会联网下载模型权重的路径；若网络无法访问 ModelScope，可改用经过 `offline-manifest.json` 校验的离线导入流程。

```powershell
# 语音转写：SenseVoiceSmall
docker compose --profile model-loader run --rm --no-deps model-loader install --preset sensevoice-small --source modelscope

# 轻量本地向量检索：multilingual-e5-small（384 维）
docker compose --profile model-loader run --rm --no-deps model-loader install --preset multilingual-e5-small --source modelscope

# 高质量本地向量检索：BGE-M3（1024 维）
docker compose --profile model-loader run --rm --no-deps model-loader install --preset bge-m3 --source modelscope
```

每次启动运行服务前，Compose 会先以只读方式运行 `model-loader verify`，重新核验 active marker、文件清单和 SHA-256。校验阶段需要读取模型文件：BGE-M3 约 2.3 GB，因此首次看到“校验模型”而非立即可用是正常的，不应把它误认为无反馈的卡顿。

### 本地语音转写（FunASR）

```powershell
docker compose --profile local-asr up --build local-asr
```

服务只监听 `http://127.0.0.1:${INTERVIEW_HELPER_LOCAL_ASR_PORT:-8011}`，健康检查为 `/health`，OpenAI-compatible 转写端点为 `/v1/audio/transcriptions`。默认单并发、单段最多 90 秒和 32 MiB；这是为了避免 CPU 机器上的单个超长音频吞掉后续面试回答。请求取消后，当前推理仍会占用唯一槽位直到实际完成，避免同一 FunASR 模型被并发调用。

### 本地向量检索（TEI）

二者只能选择其一。它们共用 `127.0.0.1:${INTERVIEW_HELPER_LOCAL_EMBEDDINGS_PORT:-8081}`，因此若误同时启动会得到明确的端口冲突，而不会悄悄切换模型。

```powershell
# 资源更省、中文与英文题库都适用
docker compose --profile local-embedding-e5 up -d local-embedding-e5

# 质量优先；CPU 也可以运行，但更适合拥有充足内存或 GPU 的设备
docker compose --profile local-embedding-bge up -d local-embedding-bge
```

两种 embedding 服务都提供 `http://127.0.0.1:8081/v1/embeddings` 和固定模型名 `interview-helper-local-embedding`。当前 BGE-M3 仅以 1024 维 dense embedding 使用；稀疏/ColBERT 多向量能力不在本阶段的 OpenAI-compatible 接口承诺范围内。服务使用受限的 CPU 基线，稍后的 GPU profile 会要求用户明确选择与显卡计算能力匹配的镜像，绝不自动拉取 CUDA 镜像。

### 在应用内启用

服务启动后，进入“设置 → 模型与 Agent”。“本地 AI 服务”区域只探测三个固定的 loopback 目标；它不会把浏览器输入的 URL、路径或 API Key 交给后端，也不会代替你启动 Docker。点击卡片右侧的检查按钮可重新探测。

探测成功后，在右侧的 Agent 角色路由中选择对应的“本地 Docker”选项：

- `SenseVoice 本地语音转写` 只能绑定到“语音转写”；
- `E5` 或 `BGE-M3` 只能绑定到“向量检索”。

这些限制同时由后端和数据库约束执行，因此本地转写不会被当成聊天模型，也不需要填写假的 API Key。服务未启动时也可以先保存绑定；发起转写时会明确报出本地服务不可达，不会静默回退到云端。

首次绑定或更换 embedding 模型后，再点击“语义索引 → 创建/重建索引”。它只创建一条后台任务：worker 会按小批次预计算长期记忆和已生成面试题的向量，并在启用前进行有限次覆盖校验，避免构建期间的编辑漏入新索引。任务构建新索引期间，旧的可用索引继续服务；完成后才原子切换。若没有已完成的向量索引、或当前题目尚未被缓存，面试仍使用既有的全文检索，不会在实时回合临时请求 embedding 服务。实时向量精排使用 120 ms 的数据库语句上限，超时会直接安全回退全文检索。若用户正在面试，索引任务会自动让出并稍后继续，因此不会把等待时间转移到候选人的回答上。

停止本地运行服务不会删除模型或数据库：

```powershell
docker compose --profile local-asr stop local-asr
docker compose --profile local-embedding-e5 stop local-embedding-e5
```

不要使用 `docker compose down -v`，它会删除 named volumes。

## 运行前提

- Docker Desktop，使用 Linux containers。
- Windows 用户如需容器 GPU，应使用 WSL 2 backend、受支持的 NVIDIA GPU 和驱动；没有 GPU 仍可走后续的 CPU 方案。
- 应用本身仍按 [本地开发与测试](local-development.md) 使用 uv 管理后端环境。Docker-only 的含义是**本地 AI 运行时**不在宿主机安装，不是替代应用的 Python/Node 开发环境。

模型来源以 ModelScope 为默认且当前唯一的联网下载路径；无法访问时，使用经过 `offline-manifest.json` 校验的离线导入。无论来源如何，模型文件不得在浏览器中下载、不得由浏览器传入任意路径，也不得在面试回合内变更。

## 从旧版 PostgreSQL 安全升级

从旧版 checkout 更新到包含 pgvector 镜像的版本前，先备份。升级保持 PostgreSQL 17 主版本和原 named volume，但容器镜像变更仍应被当作数据库升级处理。

1. 停止应用 API 和 worker，避免备份期间继续写入；保留正在运行的旧 PostgreSQL 容器。
2. 在**更新 Compose 文件之前**，为业务库和测试库分别创建可恢复的 dump：

   ```powershell
   docker compose up -d postgres
   $backup = Join-Path $PWD 'backup'
   New-Item -ItemType Directory -Force $backup

   docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/interview_helper.pre-pgvector.dump'
   docker compose cp postgres:/tmp/interview_helper.pre-pgvector.dump "$backup\interview_helper.pre-pgvector.dump"

   docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "${POSTGRES_DB}_test" -Fc -f /tmp/interview_helper_test.pre-pgvector.dump'
   docker compose cp postgres:/tmp/interview_helper_test.pre-pgvector.dump "$backup\interview_helper_test.pre-pgvector.dump"
   Get-ChildItem -LiteralPath $backup -Filter '*.pre-pgvector.dump' | Get-FileHash
   ```

3. 更新代码后，先运行 `docker compose config --quiet`，确认端口、volume 键和服务名正确。
4. 使用不删除 volume 的重启：

   ```powershell
   docker compose down
   docker compose up -d postgres
   docker compose ps
   docker compose logs --tail=100 postgres
   ```

   **绝不能使用 `docker compose down -v`，也不要使用 `docker volume rm interview-helper-postgres`。** 这两种操作会删除本地业务数据。

5. 确认应用数据库仍可连接后，执行 `python -m alembic upgrade head`。扩展由 migration 管理，不能依赖 init SQL：init SQL 只在全新 volume 初始化时运行。

如 PostgreSQL 无法启动、日志出现 data directory 或 major-version 错误，立即停止，不要删除或重建原 volume。保留原 volume、日志和 dump，在独立副本上验证恢复路径后再处理。模型 volumes 不是业务数据备份的替代品；它们可由受控 manifest 重新获得，而数据库、上传文件和加密密钥仍必须按 [隐私与本地数据](privacy.md) 备份。

## 服务边界

本地 FunASR 和 TEI 分别绑定到 loopback 地址 `127.0.0.1:8011` 与 `127.0.0.1:8081`，并使用固定的 OpenAI-compatible 协议。它们不会共享 Docker socket，不会暴露模型 volume 给前端，也不会允许网页拼接 Docker 命令、镜像名、容器参数或宿主路径。

云端 API、Docker 本地轻量 embedding、Docker 本地高质量 embedding 始终是用户可见的独立选择，而不是自动回退链路。本地服务异常时，应用应清晰提示并允许改用文本作答或用户主动选择的云端 Provider。
