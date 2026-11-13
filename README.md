# 02-batchgate（闸序）

Batchgate — 离线作业依赖编排：作业定义 → 依赖就绪检查 → 执行/失败重试 → 产出水位与对账。

与 Feedstitch（多源 Feed 归并）主链路不同，禁止做成 Feed 解析/冲突/回看换皮。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5200 |
| API | http://localhost:10200 |

## 主链

配置作业、业务域与依赖 → 触发一次编排 Run → 按域内依赖顺序执行夹具作业 → 写入尝试记录（含域）与产出水位 → 全域 + 分域报告对账

## 域隔离

- **作业有业务域**：`jobs.domain`，可在作业页查看并保存（`PATCH /api/jobs/{id}/domain`）。
- **配置期拦住**：域变更若会让该作业与其上游、或依赖它的下游形成跨域边，返回 `409 cross_domain_dependency`，并列出冲突边，不落库。
- **编排期拦住**：一次编排覆盖多域，但执行顺序只使用同域依赖；带跨域边的作业记 `blocked_domain`，写清被拦截的边，绝不执行成功。
- **分域对账**：报告与总览同时给出全域与分域的成功/失败/跳过/尝试/水位计数，并标记 `domain_consistent`（各域合计 == 全域）。
- 未引入登录/多租户权限；域只是作业的业务归属标签，不是权限边界。

## 种子说明

| 作业 | 域 | 依赖 | 说明 |
| --- | --- | --- | --- |
| ingest_orders | sales | — | 销售域入口，写出订单水位 |
| fact_order_daily | sales | ingest_orders | 同域依赖，跑通 |
| customer_profile | customer | — | 客户域独立作业，跑通 |
| marketing_attribution | marketing | ingest_orders | 演示用跨域边，编排时记 blocked_domain |

一次编排的预期结果：success 3 / blocked_domain 1 / failed 0，sales 域 2 成功 2 层。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 0-1 切口（未实现）

`dep_gate` / `retry_budget` / `watermark_reconcile` / `sla_breach`
