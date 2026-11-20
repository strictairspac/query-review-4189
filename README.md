# 冷链结算 SQL 复核台

为澄港冷链结算组保存 SQL 基准样本、独立候选改写和实库执行证据。原生 HTML/CSS/JS 前端，Python 后端执行解析、只读查询和多重集合比对。

## 真实工作场景

澄港冷链结算组共 18 人，每天从运费账单中按客户、路线、温控合格条件汇总结算。目前通过邮件传递 `coldchain-settlement.sql` 和表格复核，三个痛点是版本难定位、口径变动容易漏查、单次查询截图无法还原执行计划。上游是冷链计费账单，本站替代 SQL 改写复核和证据归档，下游是结算复核人。演示数据固定 600 条账单、3 客户、2 路线，字段与页面说明一致。

## 业务模块

1. 样本库：登记名称、口径说明与原始 SELECT，防止重名；提供温控合格、路线费用、异常温控三个起始样本。
2. SQL 复核：选择基准，编辑候选，校验后创建独立批次并入 Redis 队列。
3. 运行历史：跟踪 queued/running/passed/different/failed，失败也保留证据，并显示每批次的额度状态。
4. 证据详情：真实 EXPLAIN ANALYZE、两侧结果行、SHA-256 摘要、遗漏/新增行和 JSON 下载。
5. 额度管理：按结算组维护每日可补充的数据库用时额度。批次开跑前在本组预留上限，余额不足只在本组等待；结束后按两侧实际在库用时扣减，余量退回；可在此页补额并查看看等额度/已预留/用量待核/已结清状态与用时。
6. 管理员操作审计：记录写请求、执行账号和 HTTP 结果，包括拒绝请求。

| 角色 | 主要页面 | 权限与典型动作 |
|---|---|---|
| admin 管理员 | 全部页面、审计 | 创建样本/批次、查看/导出结果、为任意结算组补额、查看写操作审计 |
| operator 操作员 | 业务页面 | 登记 SQL、提交后台复核、查看/导出结果、为本组补额；不可查看管理员审计 |
| viewer 观察员 | 业务页面 | 只读与导出；API 层拒绝所有业务写请求和补额 |

## 每日额度口径

额度按“结算组 + 自然日”记账，账本恒等式为 `余额 = 已发放 − 已用 − 预留`。每组首次访问当日账本时获得默认发放额（`QUOTA_DEFAULT_GRANT_MS`，默认 600000 ms），可手工补额（单次上限 `QUOTA_MAX_TOPUP_MS`）。

1. 预留：worker 领取批次后、执行前，以 `QUOTA_RESERVE_MS`（默认 12000 ms，覆盖两侧各一次 EXPLAIN + SELECT 的 4×2500ms 上限）在本组充值余额中原子占用。余额不足时批次保持 `queued`（等额度），其他结算组不受影响；补额或余量退回后自动继续，无需重提。
2. 结算：两侧查询各自计时（`UsageMeter` 在 `finally` 中记录，超时也计入已实际占用时间），写入持久化用量凭据 `usage_evidence`，再按凭据合计扣减 `used_ms` 并释放预留。预留上限与实际用时的差额即时退回本组余额。
3. 跨日：结算永远记在“预留发生的那一天”的账本上，即使批次跨过零点完成；不会改扣结算当天。
4. 中断恢复：worker 启动及运行中周期性扫描租约过期的批次。若已存在用量凭据，则按凭据对账且只扣一次（凭据按 reservation 唯一，重复结算为幂等空操作）；查不到凭据则保留预留并将状态标为“用量待核”，不做静默退款。
5. 只读与限额不变：仍只允许单条 SELECT 访问 invoices，强制 READ ONLY 事务与 `query_reader` 角色，每条 statement 超时 2500ms、最多 1000 行；额度只统计耗时，不放宽任何 SQL 白名单。

## 快速启动

```bash
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
# 启动自动 seed；手动重跑不新增且不覆盖用户修改
docker compose exec -T backend python -m scripts.seed_demo
```

访问 [前端](http://localhost:18240)、[后端 OpenAPI](http://localhost:19240/docs)。数据库映射端口 17740。项目的日志、截图、测试脚本在批次根目录 `test-artifacts/gb-4101/`，不放入源码。

演示账号邮箱为 `admin@chenggang.test`、`operator@chenggang.test`、`viewer@chenggang.test`，统一密码 `ColdChain!17`。这些是本地固定演示凭据，上线前应修改 `.env` 和账号密码。

```bash
# 仅清理本项目及其演示数据
docker compose down -v --remove-orphans
```

## 一天工作剧本

1. 09:00 operator 登录，打开“低温结算汇总”样本。
2. 09:05 查看原始 SQL：按客户执行两处相关 SUM 子查询，过滤温度不高于 8 度的运费。
3. 09:10 创建 `shift-20261117` 批次，候选改写为下面的单次分组查询。
4. 09:15 提交到 worker，在运行历史刷新查看状态。
5. 09:20 打开证据，确认两侧均为 3 行、摘要相同、结果一致；展开真实计划比较循环次数与代价。
6. 09:25 把候选条件改为 `temperature > 8`，用新批次执行并查看新增/遗漏行。
7. 09:28 打开“额度管理”：确认本组当日的已发放/已用/预留/余额与两侧实际用时；量化到上限时补额，等待中的批次自动继续。
8. 09:30 下载 `review-<批次号>.json` 给结算复核人。
9. 09:40 viewer 登录复核，API 创建样本返回 403；10:00 admin 查看审计。

```sql
SELECT customer, SUM(amount) AS total
FROM invoices
WHERE temperature <= 8
GROUP BY customer
HAVING SUM(amount) > 100
ORDER BY customer
```

## 算法、输入与口径

输入是原始/候选单条 SELECT。sqlglot 解析 AST，仅准许 invoices 表、指定业务字段和 SUM/COUNT/AVG/MIN/MAX/ROUND/COALESCE/CAST。禁止多语句、INTO、锁、CTE、跨表、schema 和任意函数。解析后重新生成 PostgreSQL SQL，执行在 READ ONLY 事务中，切换 NOLOGIN `query_reader` 角色；该角色仅有 invoices 的 SELECT 权限。每条 statement 超时 2500ms，最多返回 1000 行，AST 最多 250 节点，候选 SQL 最多 6000 字符，最多 20 个排队/运行任务。

两侧均实际执行 `EXPLAIN (ANALYZE, FORMAT JSON, BUFFERS)` 与 SELECT。结果每行按列位置规范为数据库文本值，`Counter` 统计行的重复次数；列数相同且计数集合相同即 equivalent。忽略行顺序及列别名、保留重复行，NULL 保留为空值。按有序的“规范行/计数”生成 SHA-256。数值不同文本表示（如 1 与 1.0）会判不同；这是明确的保守口径。不能据固定数据集通过证明所有可能输入上的 SQL 语义等价；一次耗时不能证明优化收益。

失败态包括语法/白名单失败（提交前 422）、缺少样本（404）、重名（409）、任务配额（429）、数据库超时/运行错误（批次 failed，保存 error）。正常差异是 different，不当成异常。

## 技术栈、结构与本地开发

| 部分 | 实现 |
|---|---|
| frontend | 原生 ES modules、CSS、Nginx 1.27，olive paper 风格 |
| backend | Python 3.11、FastAPI、SQLAlchemy、sqlglot、PyJWT |
| worker | 独立 Python 进程，线程池并发执行，Redis 唤醒 + DB 状态恢复队列，租约防重入，心跳文件 |
| db / cache | PostgreSQL 16、Redis 7 |
| auth | JWT 8 小时有效，PBKDF2 SHA-256 加盐密码 |

`backend/src/` 按 config/models/schemas/services/controllers/routes/middlewares/utils/constants 分工；`backend/scripts/seed_demo.py` 管理幂等初始化；`backend/migrations/README.md` 说明 schema v2（含额度账本）。`frontend/src/` 按 api/stores/types/components/common/hooks/pages/router/utils/constants 分工。实体链分别为 sample、batch、evidence，各自有模型、schema、服务、路由、API、store 和页面。

后端本地：创建虚拟环境并安装 `backend/requirements.txt`，先用 Compose 启动 db/redis，设置本地可达的 DATABASE_URL/REDIS_URL/JWT_SECRET，进入 backend 执行 `uvicorn src.main:app --reload`；worker 需 `/artifacts` 可写目录并执行 `python -m src.worker`。最省事的开发方式是修改源码后 `docker compose up -d --build`。前端通过 Nginx 的 `/api/` 去前缀代理访问后端，禁止双重 `/api/api`。

环境变量：COMPOSE_PROJECT_NAME（容器命名）、FRONTEND_PORT/BACKEND_PORT/DB_PORT（宿主端口）、POSTGRES_PASSWORD（演示库密码）、JWT_SECRET（签名密钥）、SEED_DEMO（默认 true）。后端容器另接收 DATABASE_URL 和 REDIS_URL。额度策略可通过 QUOTA_RESERVE_MS、QUOTA_DEFAULT_GRANT_MS、QUOTA_MAX_TOPUP_MS、QUOTA_LEASE_SECONDS、QUOTA_WAIT_RETRY_SECONDS、WORKER_CONCURRENCY 调整；ARTIFACTS_DIR 可改心跳与证据副本目录。5 个服务均有 healthcheck；数据库、Redis、共享证据各自使用命名卷。

共享枚举位置：角色与任务状态在 `backend/src/constants/enums.py` 和 `frontend/src/constants/enums.js`；模型/鉴权服务/worker 消费后端值，状态标签与前端权限消费前端值。源数据 invoice status 为账单业务值 settled/pending，与任务状态独立。

## 已知边界

- 小型工作流没有样本删除和版本编辑；新口径新建样本/批次，旧记录保持独立。
- 使用固定业务数据和单 worker 进程（进程内多线程并发）；不对接生产数据库，不部署生产高可用。
- 并发执行依赖数据库行锁与批次租约列；跨进程多 worker 时账本更新仍正确，但租约目前只在领取时续期，未做跨进程长任务压测，生产建议单 worker 进程 + `WORKER_CONCURRENCY` 调线程数。
- 用量凭据按批次只保留一份：同一批次若被重新执行，沿用首次的凭据扣减，不重复计入。
- 只允许受限 SQL 子集；不支持 CTE、窗口、跨表联接和用户自定义函数。
- 查询会执行两次（一次 EXPLAIN ANALYZE，一次取结果）；数据源固定只读，避免两次间口径变化。
- 后端迁移/seed 角色为本地数据库管理员；执行用户 SQL 时强制切换受限只读角色。生产应拆分独立迁移凭据。
- Web 字体来自 Google Fonts，离线会使用 serif/monospace 回退；不影响功能。

License: MIT，见 LICENSE。
