# CLAUDE.md

面向 A 股研究与模拟交易的本地智能体工作台。功能说明看 [README.md](README.md)，这里只记**读代码不容易发现、但踩了会疼**的东西。

## 环境

```bash
.aniu/local/.venv/bin/python    # 后端解释器，不是系统 python
```

本地 venv 是 Python 3.14，CI 跑的是 3.12。**本地绿不等于 CI 绿**，用到新语法时留意。

## 架构约束（由测试强制，违反直接红）

[`backend/tests/architecture/test_import_boundaries.py`](backend/tests/architecture/test_import_boundaries.py) 是这套约束的唯一权威。分层规则（左边可以 import 右边）：

```
llm        → llm                                    完全独立，测试会拷到临时目录裸跑
agent      → agent, llm
stock_api  → stock_api, business
business   → business, llm
infra      → infra, agent, business, llm, stock_api
api        → api, business
bootstrap  → 全部（组装根）
```

除分层外还有五条硬规则：

| 规则                              | 说明                                                |
| --------------------------------- | --------------------------------------------------- |
| 单文件 ≤ 1000 行                  | 超了就拆模块                                        |
| 禁止 import 环                    | 新 feature 模块要注意双向依赖                       |
| `main.py` 只能有一行 import       | 它是纯入口                                          |
| API 路由禁止 `response_model=Any` | 契约必须具体                                        |
| repository ports 按 feature 分散  | `business/shared/ports.py` 只允许有 `CommitterPort` |

**新增 business feature 模块时最容易踩的是 import 环**：如果 A 要调用 B，就让 B 完全不认识 A，共享的东西放 `business/shared/`。

## 质量门禁

```bash
.aniu/local/.venv/bin/python -m ruff check backend scripts
.aniu/local/.venv/bin/python -m mypy backend          # strict 模式
.aniu/local/.venv/bin/python -m pytest backend/tests -q
npm --prefix frontend run lint -- --max-warnings=0
npm --prefix frontend test
npm --prefix frontend run build
```

**改了后端 schema 必须重新生成前端类型**，否则 CI 的 `api:check` 会失败：

```bash
npm --prefix frontend run api:generate
```

`api:check` 本地跑会因为「改动未提交」而失败，这是正常的——它对比的是 HEAD。真正要验的是生成幂等：连跑两次 `api:generate`，第二次应无变化。

## 陷阱

**别在 `backend/` 下建虚拟环境。** 架构测试用 `rglob("*.py")` 遍历整个 `backend/`，会把 venv 里 pip 的 vendor 代码算进去，导致「超 1000 行」和「import 环」双双误报。venv 只放 `.aniu/local/.venv`。

**改了定时任务要重启后端。** cron 在应用启动时注册，`--reload` 只热更新 Python 代码，不会重新排期。

**切分支前先停掉前后端。** 带 `--reload` 的服务会跟着分支切换加载/丢失文件，切到不含某模块的分支时后端会因为 import 失败而起不来。同步上游的完整顺序是：停服务 → `git checkout main` → 同步 → 切回功能分支 → 重启。

**后端日志在 `.aniu/local/backend.log`。** 如果用的是自己写的前台启动脚本，注意它是用 `>` 还是 `>>` 重定向——用 `>` 的话每次重启都会清空日志，排查历史问题前先确认日志还在。

**日志脱敏有边界**（[`infra/observability/log_config.py`](backend/infra/observability/log_config.py)）：

- 凭证可能藏在 **URL 路径或 query** 里（Server酱、企业微信群机器人），不只在 header
- httpx 打日志时传的是 `httpx.URL` **对象**不是 str，`redact_value` 必须能处理非字符串
- **格式串不能整体脱敏**：`logger.info("key=%s", v)` 里的 `%s` 会被当成密钥值替换掉，导致占位符消失、`msg % args` 抛 `TypeError`。只有「无 args 的消息」才可以整体脱敏

新增任何对外 HTTP 集成时，检查一遍凭证会不会顺着日志漏出去。

## 领域要点

**运行流水线是两阶段**（`business/runs/pipeline_stages.py`）：

```
Run      研究 + 判断 + 交易 + 产出 Markdown 报告   ← 唯一能调工具、能写副作用的阶段
Summary  把报告渲染成 HTML
```

README 里「研究、决策、交易、总结等阶段」是旧描述，这四件事现在都在 Run 内部完成。`Dream`（夜间记忆整理）是独立任务，不在这条 FSM 里。

**下单 ≠ 成交。** `trade` 工具返回 `orderId` 只代表委托被受理，限价单可能永远不成交。成交只能从 `account_orders_cache` 的 `filled_quantity` 观测，所以：

- 下单/撤单 → 工具调用回调里实时发现
- 成交 → 账户刷新时 diff（交易时段内每 30 分钟，见 `ACCOUNT_REFRESH_MINUTES`）

委托缓存每次刷新删表重建，所以任何「已处理过某笔委托」的状态都不能存那儿，否则每次刷新都会重来一遍。

## 这是 fork

`origin` = 自己的 fork，`upstream` = 原作者。**功能开发一律开 feature 分支**，`main` 保持成上游的干净镜像。这样同步上游（`git merge upstream/main`）永远是 fast-forward，不会有合并冲突——一旦在 `main` 上直接改过代码，两条线就分叉了，之后每次同步都可能要手动解冲突。

## 修改前请先跑起来

本仓库最近三个 bug（历史通知轰炸、密钥进日志、样例数据缺字段）**单元测试全绿时都没暴露**，都是用真实数据、真实凭证跑起来才发现的。涉及外部服务、真实账户数据或凭证的改动，写完测试之后再实际跑一遍。
