# MX fixtures

脱敏的离线测试数据，不含 URL、请求头或 API 密钥。

- `mx_stock_screen.json`: 妙想 `/api/claw/stock-screen` 对「光模块 CPO 概念成分股」的回答（2026-09-16 10:30，那次运行交给 Summary 的材料里留下的开头部分）。条件、只数（111）和 `partialResults` 里前 10 只的表格是原样；`id`、`traceId`、`requestId` 换成占位值，`executionTime` 按表头时间补写。`allResults` 原件是 111 只股票的全部字段，约 15 万字，这里只留两列的列定义、不留行。`title` 取自当次查询的原话。
