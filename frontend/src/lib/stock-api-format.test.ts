import { describe, expect, it } from "vitest";

import { formatStockApiParameters, formatStockApiProvider } from "./stock-api-format";

describe("stock API log labels", () => {
  it("names 同花顺 and reads the signal tools' arguments in Chinese", () => {
    expect(formatStockApiProvider("ths")).toBe("同花顺");
    expect(
      formatStockApiParameters({ action: "pool", pool: "limit_up", trade_date: "2026-09-28" }),
    ).toBe("动作：涨跌停池；池子：涨停池；交易日：2026-09-28");
    expect(formatStockApiParameters({ action: "dragon_tiger", symbol: "600487" })).toBe(
      "动作：龙虎榜；股票代码：600487",
    );
  });

  it("names the day the tool fell back from", () => {
    expect(
      formatStockApiParameters({
        action: "dragon_tiger",
        trade_date: "2026-09-24",
        trade_date_fallback_from: "2026-09-28",
      }),
    ).toBe("动作：龙虎榜；交易日：2026-09-24；原定交易日（无数据）：2026-09-28");
  });

  it("leaves other tools' actions as they were", () => {
    expect(formatStockApiParameters({ action: "stocks", limit: 20 })).toBe(
      "动作：stocks；数量上限：20",
    );
  });
});
