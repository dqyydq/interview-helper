# CI 可复现性修复设计

## 目标

让 GitHub Actions 在与本地运行时一致的 PostgreSQL 扩展环境中执行迁移、测试与格式检查，确保公开仓库显示真实、可复现的工程状态。

## 范围

- 将 CI 后端 job 的 PostgreSQL 服务镜像替换为项目 Compose 已使用的固定 pgvector PostgreSQL 17 镜像。
- 使用锁定版本的 Ruff 格式化现有 Python 文件，使 `ruff format --check .` 通过。
- 验证 Alembic 升级/降级、后端测试、前端 lint、测试与生产构建。

## 非目标

- 不修改 API、数据库 schema、领域逻辑、前端路由或产品行为。
- 不削弱 CI：保留格式、迁移、测试、依赖审计和密钥扫描。
- 不添加部署、账号系统、监控或新的第三方服务。

## 设计决策

CI 服务镜像与 `docker-compose.yml` 使用相同的不可变 pgvector 镜像摘要。这样 `0008_enable_pgvector` 的 `CREATE EXTENSION vector` 在本地与 CI 的能力边界一致，避免普通 PostgreSQL 镜像导致迁移失败。

Ruff 只执行机械格式化。格式变更与 CI 镜像修正放在独立提交中，便于审阅，也不改变运行时语义。

## 验收标准

1. `uv run ruff format --check .` 与 `uv run ruff check .` 通过。
2. GitHub Actions 的 PostgreSQL 服务可提供 `vector` 扩展，迁移升级、回退一级、再升级均通过。
3. 后端测试、前端 lint、Vitest 和生产构建通过。
4. 工作树除该修复预期文件外无新增业务改动。
