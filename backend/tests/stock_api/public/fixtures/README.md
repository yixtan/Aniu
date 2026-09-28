# Public stock fixtures

这些样本均为脱敏、离线测试数据，不包含 URL、Host、请求头或 API 密钥。

- `financial_latest.json`: `RPT_PCF10_FINANCEMAINFINADATA`，最新财务字段。
- `financial_quarterly.json`: `RPT_F10_QTR_MAINFINADATA`，季度财务字段。
- `shareholders.json`: `RPT_F10_EH_HOLDERNUM`，股东户数字段。
- `valuation.json`: `RPT_STOCKVALUATIONTANTILE` 的 4 个指标 × 4 个窗口。
- `industry_comparison.json`: `RPT_F10_INDUSTRY_COMPARED`，同行身份字段。
- `operating_indicators.json`: `RPTA_DATA_IF_INDICATOR`，经营指标字段。
- `forecast_institutions.json`: `RPT_HSF10_RES_ORGPREDICT`，机构预测字段。
- `web_respredict.json`: `RPT_WEB_RESPREDICT`，盈利预测汇总和评级字段。
- `eastmoney_batch_quote.json`: 东方财富批量行情字段。
- `tencent_kline.json`: 腾讯 K 线响应。
- `tencent_quote.txt`: 腾讯实时行情文本，供分时昨收解析。
- `tencent_intraday.json`: 腾讯分时响应。
- `tencent_ranking.json`: 腾讯排行真实字段。
- `sina_ranking.json`: 新浪排行响应。
- `sina_money_history.json`: 新浪 `MoneyFlow.ssl_qsfx_zjlrqs`，按日期倒序的个股资金流（600487，09-23、09-24、09-28 三个交易日）。
- `sina_money_today.json`: 新浪 `MoneyFlow.ssi_ssfx_flzjtj`，个股当天按单子大小分档的累计流入流出（600487，2026-09-28）。
- `sina_money_ranking.json`: 新浪 `MoneyFlow.ssl_bkzj_ssggzj`，全部 A 股按主力（r0）净流入排行（2026-09-28，前三行里两只是 ETF）。
- `sina_sector_money.json`: 新浪 `MoneyFlow.ssl_bkzj_bk`，新浪行业板块按净流入排行（2026-09-28）。
- `datacenter_empty.json`: 东方财富数据中心对没有结果的查询的原样回答（`RPT_DAILYBILLBOARD_DETAILSNEW`，600519，2026-09-21 至 09-28 未上龙虎榜；2026-09-29 02:31 取得，HTTP 200）。
- `em_lhb_stock_600487.json`: `RPT_DAILYBILLBOARD_DETAILSNEW`，600487 近 90 天的上榜记录（2026-08-25、08-05 两条，每条一个上榜原因；2026-09-29 03:00 取得，原样保存）。
- `em_lhb_buy_seats_600487_20260825.json`: `RPT_BILLBOARD_DAILYDETAILSBUY`，600487 在 2026-08-25 的买入前五席位（含沪股通专用、机构专用；每行只有买入一侧，SELL 为 null）。
- `em_lhb_sell_seats_600487_20260825.json`: `RPT_BILLBOARD_DAILYDETAILSSELL`，600487 在 2026-08-25 的卖出前五席位（沪股通专用、中金上海分公司两边都在榜；每行只有卖出一侧，BUY 为 null）。
- `em_lhb_market_20260928.json`: `RPT_DAILYBILLBOARD_DETAILSNEW` 不带代码过滤，2026-09-28 全市场龙虎榜，67 行删到 14 行（8 只股票），保留同一只股票多条原因、北交所 920xxx、风险警示板和科创板的行；result.count 改为 14。
- `em_lift_600487.json`: `RPT_LIFT_STAGE`，600487 限售解禁批次，过去和已排定的都在一页里（2026-09-29 01:46 取得；11 批保留 5 批：2028-06-20、2027-06-21 两批股权激励未来批次，外加 2026-07-08、2022-06-16、2021-06-16 三批历史批次）。`FREE_SHARES` 是解禁后的流通股总数，不是本批数量。
- `em_rzrq_600487.json`: `RPTA_WEB_RZRQ_GGMX`，600487 两融逐日明细，最新 4 个交易日（2026-09-24 至 09-21；09-28 那行到 2026-09-29 凌晨仍未公布）。09-24 这行和上交所官方数据分毫不差。
- `em_lift_300750.json`: `RPT_LIFT_STAGE`，300750 限售解禁批次（2026-09-29 02:58 取得；15 批保留 3 批：2024-09-24、2021-06-11 首发原股东限售、2019-06-11），之后没有已排定的批次。
- `em_rzrq_300750.json`: `RPTA_WEB_RZRQ_GGMX`，300750 两融逐日明细，最新 2 个交易日（2026-09-24、09-23；2026-09-29 02:58 取得）。
- `em_pool_limit_up_20260928.json`: 东方财富 push2ex `getTopicZTPool` 涨停池的原样回答（2026-09-28 收盘后，33 只全部保留，用来核对连板梯队 {1:26, 2:3, 3:3, 5:1} 和炸板率 25.0%；2026-09-29 01:45 取得，HTTP 200）。
- `em_pool_broken_20260928.json`: 东方财富 push2ex `getTopicZBPool` 炸板池的原样回答（2026-09-28，11 只全部保留；8 只的 `zttj` 是 {0, 0}）。
- `em_pool_limit_down_20260928.json`: 东方财富 push2ex `getTopicDTPool` 跌停池（2026-09-28，56 只里留了 5 只，`tc` 仍是原值 56；含 `fba` 大于全天成交额的 603230，和连续两天跌停的 600664）。
- `em_pool_previous_limit_up_20260928.json`: 东方财富 push2ex `getYesterdayZTPool` 昨日涨停池的原样回答（用 2026-09-28 请求，列的是 09-24 的涨停股；52 只全部保留，含北交所 920748；收在涨停价的 7 只）。
- `ths_pool_summary_20260928.json`: 同花顺涨停揭秘 `limit_up_pool` 响应里的 `data` 对象（2026-09-28，33 只；每行的 `time_preview` 分时序列已去掉；603396 的 `high_days` 为 null）。
- `ths_getharden_20260928.json`: 同花顺涨停归因（getharden）原样信封，2026-09-28，35 行里取 7 行（含 ST万邦、*ST华幸），去掉了 `longhubangurl` 链接模板；`chengjiaoe` 单位是万元，`chengjiaoliang` 是手。
- `ths_limit_up_pool_20260928.json`: 同花顺涨停揭秘（limit_up_pool）原样信封，2026-09-28，33 行里取 8 行，`time_preview` 截成前 4 个点；`limit_up_count`、`limit_down_count` 汇总保持原样（涨停 33、跌停 56）。
- `ths_hot_list_day.json`: 同花顺人气热榜日榜原样信封，2026-09-29 01:45 的快照（涨跌幅是 09-28 收盘），100 行里取名次 1–5 和 33。
