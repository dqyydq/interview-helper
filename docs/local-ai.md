# Docker-only 本地 AI

> 状态：Docker 交付底座与受校验的模型 loader 已建立；本地 ASR、embedding 推理服务和设置页仍在后续阶段实现。

Interview Helper 的本地语音转写与本地 embedding 只会通过 Docker 容器运行。宿主机不需要、也不应为本应用安装 FunASR、TEI、PyTorch、CUDA Python 包或模型运行时。云端模型仍然是独立、显式选择的选项；本地服务故障时应用不得把音频、简历或回答静默发送到云端。

## 当前 Compose 行为

- PostgreSQL 使用不可变的 `pgvector/pgvector@sha256:3e8b3adfd27b5707128f60956f62a793c3c9326ea8cfaf0eab7adccb5d700b21` 镜像（pgvector 0.8.1 / PostgreSQL 17，多架构 OCI index），并保留原有 named volume 键 `interview-helper-postgres`。
- PostgreSQL 只映射到 `127.0.0.1:${POSTGRES_PORT:-5432}`，不会暴露到局域网或公网。
- `interview-helper-models` 保存将来已校验的模型文件，`interview-helper-model-state` 保存下载和校验状态；它们与业务数据库 volume 分离。
- `model-loader` 位于显式的 `model-loader` profile。普通 `docker compose up -d postgres` 不会启动它，也不会拉取镜像、构建镜像或下载模型。
- loader 只能安装受支持 preset：SenseVoiceSmall、multilingual-e5-small 和 BGE-M3。每个 preset 固定到不可变的 ModelScope commit；它会先检查模型 volume 的保守可用空间，再写入 staging 区、逐文件校验 SHA-256，成功后才原子写入 active marker；离线包还必须带匹配的 `offline-manifest.json`。同一 preset 的安装会通过共享 state volume 串行化，避免重复点击或两个容器竞争覆盖 active 状态。
- 本阶段尚未提供本地 FunASR、TEI、设置页或 pgvector Alembic migration。不要手工执行 `CREATE EXTENSION vector`。

只检查 Compose 文件时使用：

```powershell
docker compose config --quiet
docker compose --profile model-loader config --quiet
```

以上检查不会启动或拉取容器。当前 loader 会先把模型下载或导入到 staging、校验清单和哈希，再原子地标记为已验证；推理服务接入后还会增加真实推理冒烟验证。面试过程中不会下载模型、预热模型、重嵌入或建立向量索引。

`model-loader` 的 `status` / `verify` 命令不会访问网络；`install` 是唯一会从 ModelScope 下载权重的操作。开发人员需要显式运行该命令才会构建 loader 镜像并进入下载流程。推理服务尚未接入前，不要把“权重已校验”误认为“本地转写或检索已经可用”。

如果主机被关闭或 loader 被强制终止，确认**没有同一 preset 的安装仍在运行**后，才可以显式清理其锁；该命令不会删除模型、active marker 或 PostgreSQL 数据：

```powershell
docker compose --profile model-loader run --rm --no-deps model-loader recover-lock --preset sensevoice-small --confirm-no-active-install
```

普通 `install` 永远不会自动抢占锁，因为低带宽下载大型模型可能持续很久；后续应用设置页会把 `install_in_progress` 作为“正在安装，请稍后刷新”的可恢复状态处理。

## 运行前提

- Docker Desktop，使用 Linux containers。
- Windows 用户如需容器 GPU，应使用 WSL 2 backend、受支持的 NVIDIA GPU 和驱动；没有 GPU 仍可走后续的 CPU 方案。
- 应用本身仍按 [本地开发与测试](local-development.md) 使用 uv 管理后端环境。Docker-only 的含义是**本地 AI 运行时**不在宿主机安装，不是替代应用的 Python/Node 开发环境。

模型来源会以 ModelScope 为默认优先级，Hugging Face 仅作为用户明确选择的备用来源；也会支持经过校验的离线导入。无论来源如何，模型文件不得在浏览器中下载、不得由浏览器传入任意路径，也不得在面试回合内变更。

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

5. 确认应用数据库仍可连接后，再在未来包含正式 Alembic migration 的版本执行 `python -m alembic upgrade head`。扩展由 migration 管理，不能依赖 init SQL：init SQL 只在全新 volume 初始化时运行。

如 PostgreSQL 无法启动、日志出现 data directory 或 major-version 错误，立即停止，不要删除或重建原 volume。保留原 volume、日志和 dump，在独立副本上验证恢复路径后再处理。模型 volumes 不是业务数据备份的替代品；它们可由受控 manifest 重新获得，而数据库、上传文件和加密密钥仍必须按 [隐私与本地数据](privacy.md) 备份。

## 后续本地服务边界

后续实现会把本地 FunASR 和 TEI 分别绑定到 loopback 地址（计划为 `127.0.0.1:8011` 与 `127.0.0.1:8080`），并让 FastAPI 通过固定的 OpenAI-compatible 协议访问。它们不会共享 Docker socket，不会暴露模型 volume 给前端，也不会允许网页拼接 Docker 命令、镜像名、容器参数或宿主路径。

在这些服务和 health check 实际落地前，请继续使用文本回答或已显式配置的云端 Provider。云端 API、Docker 本地轻量 embedding、Docker 本地高质量 embedding 将始终是用户可见的独立选择，而不是自动回退链路。
