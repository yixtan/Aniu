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

### 本地 git 钩子（推荐配置，不随仓库分发）

`.git/hooks/` 不被跟踪，所以新克隆的仓库没有这些钩子，需要各自配置。当前这台机器上装了两个：

| 钩子         | 跑什么                                             | 耗时     |
| ------------ | -------------------------------------------------- | -------- |
| `pre-commit` | ruff + mypy（改了 `.py` 时）、eslint（改了前端时） | 1 秒内   |
| `pre-push`   | pytest（改了 `.py` 时）、vitest（改了前端时）      | 约 25 秒 |

慢的测试放在推送前而不是提交前，是为了让提交保持无感——太慢的钩子会被绕过，等于没有。两者都按「本次改了什么」决定跑什么，只改文档不会触发任何检查。

临时跳过：`git commit --no-verify` / `git push --no-verify`。

## 陷阱

**模型的「最大输出」不能设成等于上下文窗口。** Summary 阶段的输入预算是
`上下文窗口 − 最大输出 − 2000`（[`stages/summary_stage.py`](backend/business/runs/stages/summary_stage.py)）。两者相等时预算为负，
`build_summary_stage_payload` 抛出 `summary input budget is empty`，编排器捕获后**静默降级**成
Markdown：运行仍标记为 COMPLETED，只有 `summary_render_mode` 从 `html` 变成
`markdown`，页面上看不出任何异常。本仓库曾因此连续 69 次运行都没生成过 HTML 总结。

排查方法——正常应为 `html`：

```sql
SELECT summary_render_mode, COUNT(*) FROM strategy_runs
WHERE summary IS NOT NULL GROUP BY 1;
```

降级原因记录在该次运行 `trace_json` 的 Summary 阶段里，step_id 为 `markdown_fallback`。

**国内镜像源会让本地和 CI 看到不同的世界。** `registry.npmmirror.com` 不提供 audit 数据——
裸跑 `npm audit` 会拿到一个空的 error，看起来像「没有漏洞」，而 CI 走官方源会真实报出来。
`scripts/audit.mjs` 因此把源写死成官方地址，所以 `npm --prefix frontend run audit` 在任何
镜像配置下都能给出真实结果。手动查同理，要显式指定源：

```bash
npm --prefix frontend audit --registry=https://registry.npmjs.org
```

`~/.npmrc` 里出现 `allow-scripts=` 会让所有项目级 npm 操作（含 `npm audit`）直接报
`EALLOWSCRIPTS` 失败。要给全局包放行安装脚本，在那一条安装命令上加参数，别写进用户级配置。

pip 同理：用 pip-compile 重建 `requirements.lock` 时，本地镜像配置会被写进文件头，
必须手动删掉 `--index-url` / `--trusted-host` 两行再提交。

**新增一个设置字段，它要在四个地方同时存在**，少一个就是静默丢失：

```
API 请求模型 → 域模型（含 as_dict / from_mapping）→ DTO → API 响应模型
```

漏掉 DTO 那一层最难发现：值**写进了数据库**，只是读不回来。界面显示为空，看起来像没保存；
而下一次保存会把读回来的空串存回去——**那时才真的丢失**。

所以测试必须覆盖读回来那一步：存进去 → 读响应 → 重新 GET 一次。
只断言「保存返回 200」会通过，因为写入那半边从来没坏（`watchlist_prompt` 就是这么漏的）。

**别在 `backend/` 下建虚拟环境。** 架构测试用 `rglob("*.py")` 遍历整个 `backend/`，会把 venv 里 pip 的 vendor 代码算进去，导致「超 1000 行」和「import 环」双双误报。venv 只放 `.aniu/local/.venv`。

**改了定时任务不用重启，改了调度开关才要。** 保存交易任务（`ScheduleAppService._sync` →
`sync_schedule`）和保存梦境时间（`settings` 路由 → `sync_memory_dream_job`）都会当场重新排期，
日志里能看到对应的 `Added job`，时间戳就是保存的那一秒。交易任务同步失败还会写进那条计划的
`sync_error` 字段，界面上看得见。真正只在启动时读一次的是 `ANIU_ENABLE_SCHEDULER`（在
`RuntimeConfig.from_env()` 里），关着时所有触发直接 return。

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

**全局提示词拼在三个阶段前面，不只是 Run。** Run 和 Summary 走
[`run_entity.py`](backend/business/runs/run_entity.py) 的 `_compose_stage_prompt`，Dream 走
[`dream_agent.py`](backend/infra/integrations/dream_agent.py)，都是 `全局 + "\n\n" + 阶段`。所以判据是
**只有对三个阶段都成立的东西才该放全局**——交易目标、仓位口径这类只属于 Run，写进全局
就会连 Summary（只渲染 HTML）和 Dream（只整理记忆）一起吃到。给 Dream 灌「唯一目标是
收益最大化」尤其别扭：它在判断该删哪条经验时会偏向进攻性的那些。

**改提示词前先看看记忆库里有没有同一件事。** 记忆是 agent 自己写的，写什么由提示词决定，
两边容易各说各的：本仓库出现过 `T+1 当日买入不可卖` 被当成「经验」验证后写进记忆，而那
是 A 股制度，属于常量，本该在提示词里一次说清。判断方法——制度和口径进提示词，
「什么情况下该怎么做」才是记忆。

快照存的是**未拼装**的阶段提示词，拼装发生在 `settings_for_stage()` 读取时，所以查历史运行
实际发出了什么，不能只读 `snapshot_json`——那里少了全局那一段。

**记忆的写入与修剪是分开的。** Run 阶段的 `memory_write` 只有 `create`/`update`，
删除权限只给夜间的 Dream（`AUTHORING_OPERATIONS` vs `ALL_MEMORY_OPERATIONS`，见
[`memory_agent_tools.py`](backend/infra/integrations/memory_agent_tools.py)）。判断一条经验是重复的还是「尚未复现」，
需要跨天的视角，而一次运行只看得见自己。工具描述会随权限变化，schema 和运行时双重拦截。

**梦境整理哪一天，看运行记录，不看时钟。** 每次触发挑「最近 3 个有运行的交易日里还没
completed 梦境的那些」，最新的优先。所以：

- 漏一晚（关机、崩溃）不会永久遗漏，下次触发自动补上——旧实现是 `今天 − 1 天`，错过就再也回不去
- 零运行的日子（周末、假期）不占额度，也不会白跑一次 agent
- FAILED 的梦境下次触发会自动重试
- 回溯窗口和单次上限是**同一个数**（`DREAM_BACKFILL_DAYS`）。窗口比上限宽的话，够不到的那几天会在滑出窗口前静默过期

执行时间限定在 **16:00 ~ 次日 08:00**，收盘之后、开盘之前。盘中整理会把一个还在进行的交易日
标记成已完成，之后的运行就再也不会被读到。窗口是后加的，所以 `settings_repo` 的加载路径对超出
范围的旧值会降级成默认值并打 warning——否则老配置会让应用起不来。

**可选的运行上下文，空的时候整段不发。** 关注清单随 `runtime_context` 送到 Run 阶段，
和 `market_session_open` 并排，不做成 Agent 工具——它上限 10 条且每次都要全看，工具调用
要多一次往返、每次请求都带工具定义，而且**可以不被调用**。（记忆是相反的情况：几十上百条、
按关键词检索，那才适合工具。）

清单为空时，清单本身和它那段补充提示词都不拼进消息里。让模型对着空列表「考虑」，
只会请它说一句「无可关注」，白花 token。

清单**实时读取，不进运行快照**：它是数据（像行情），不是配置，早上加的公司下午的运行就该看见。
补充提示词则是配置，随其他阶段设置进快照。读清单失败不会让运行失败——它是参考，
不是运行依赖的输入。

**下单 ≠ 成交。** `trade` 工具返回 `orderId` 只代表委托被受理，限价单可能永远不成交。成交只能从 `account_orders_cache` 的 `filled_quantity` 观测，所以：

- 下单/撤单 → 工具调用回调里实时发现
- 成交 → 账户刷新时 diff（交易时段内每 30 分钟，见 `ACCOUNT_REFRESH_MINUTES`）

委托缓存每次刷新删表重建，所以任何「已处理过某笔委托」的状态都不能存那儿，否则每次刷新都会重来一遍。

**未成交的委托是一个还没生效的决策，它不会随判断改变而失效。** 限价单挂着不动，
价格碰到就自动成交，不需要谁再同意一次。所以一次运行推翻了先前的看法，却不撤掉对应的挂单，
等于让已经作废的结论保留着执行权。

2026-09-11 真实发生过：10:00 挂了 `601869 长飞光纤 买入 100 @ 440`，当天的运行得出
「筹码分散、追高赔率差，放弃，不追」，挂单没撤——而那天的价格区间是 428–457，440 就在里面。

**诊断这类问题先看 trace，别猜。** 那次运行**调过** `query_portfolio` 并且指令里明确写了
「委托」，报告里却一次都没提它（「委托」「挂单」「440」各 0 次）。所以它不是没查，是查到之后
在结论里沉默了——往提示词里加「记得查看委托」毫无作用。**这一类的失败模式是沉默，不是判断错**，
对应的写法是要求逐笔表态（撤还是留、留的条件是什么），沉默即不合规，这样报告里一眼能看出
它有没有做。

位置也有讲究：这件事属于「分析账户」那一步，不属于「做交易决定」那一步。撤掉一个已失效的挂单
不是新决策，是清理旧决策；放到后面就会重演同一次失败——结论已经写完了，才想起来还有笔单子。

**运行失败不会自动重跑，这是故意的。** 会自动重试的只有最里面那层：

| 层                                                 | 重试                                                                                                                  |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| LLM 请求（[`llm/retry.py`](backend/llm/retry.py)） | 最多重试 2 次（共 3 次请求），指数退避带抖动。只重试限流、超时、网络、5xx、空响应；上下文溢出不重试——再发一遍也一样大 |
| 运行任务（`run_jobs`）                             | **不重试。** 失败即 `FAILED`，`available_at` 从不往后推，代码里没有重新入队这条路                                     |
| 调度（APScheduler）                                | `misfire_grace_time` 是默认的 1 秒，错过的时点直接丢，不补跑                                                          |

租约（20 秒，每 5 秒心跳续约）过期后那行确实能被重新认领，但 worker 一看 `attempt > 1` 就标成
`INTERRUPTED`，理由写在错误信息里：「运行租约已过期；为避免重复交易，任务不会自动重放」。启动时的
`_recover_stale_run_jobs` 同理，只收尸不重放。所以 `DEFAULT_MAX_ATTEMPTS = 3` 永远够不到，它是保险的保险。

分界线是**副作用**：一次运行会真的下委托，崩在半路时没人知道哪些委托已经发出去了；而请求还没发出去，
重发是安全的。将来真要做重试，正确的形状不是「失败就重跑」，而是「trace 里一次带副作用的工具
（`trade` / `cancel` / `memory_write`）都没调过，才允许重新入队一次」。

## 这是 fork

`origin` = 自己的 fork（`yixtan/Aniu`），`upstream` = 原作者（`AnacondaKC/Aniu`）。

**功能开发一律开 feature 分支**，走 PR 合进 `main`。一个分支只装一件能独立回滚的事——判据是「做错了想不想整个撤掉」，不是按时间或按大小切。

**`main` 是本仓库的开发主线，不是上游的镜像。** 它早就分叉了（2026-09-08 时领先上游 28 个提交），所以：

- 同步上游（`git merge upstream/main`）**会产生真正的合并提交，不是 fast-forward**
- 上游改到你改过的文件时，可能要手工解冲突

这是 fork 长期演化的正常形态，不用试图恢复成镜像。代价是同步变麻烦，收益是克隆 `main` 就能拿到完整的东西——分享给别人时靠的就是这一点，所以 `main` 要保持随时可用。

安装脚本已指向本 fork（`install-linux.sh` 的 `REPO_DEFAULT` 和 README 的 curl 地址两处都要一致，只改一处不起作用）。

## 修改前请先跑起来

本仓库最近三个 bug（历史通知轰炸、密钥进日志、样例数据缺字段）**单元测试全绿时都没暴露**，都是用真实数据、真实凭证跑起来才发现的。涉及外部服务、真实账户数据或凭证的改动，写完测试之后再实际跑一遍。
