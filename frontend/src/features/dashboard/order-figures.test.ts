import { describe, expect, it } from "vitest";

import { orderFigures } from "@/features/dashboard/order-figures";

type Order = Parameters<typeof orderFigures>[0];

function order(overrides: Partial<Order> = {}): Order {
  return {
    order_id: "order-1",
    symbol: "688008",
    stock_name: "澜起科技",
    direction: "BUY",
    quantity: 300,
    order_price: 185,
    filled_quantity: 0,
    filled_price: null,
    status: "PENDING",
    submitted_at: "2026-09-16T05:21:00Z",
    updated_at: "2026-09-16T05:21:00Z",
    ...overrides,
  };
}

describe("orderFigures", () => {
  it("shows what a resting order offered, not the fill it has not had", () => {
    // Every row on screen on 2026-09-16 was like this: 0 shares and no price.
    expect(orderFigures(order())).toEqual({
      quantity: 300,
      price: 185,
      filled: false,
    });
  });

  it("shows the fill once there is one", () => {
    const figures = orderFigures(
      order({ filled_quantity: 300, filled_price: 184.2, status: "FILLED" }),
    );

    expect(figures).toEqual({ quantity: 300, price: 184.2, filled: true });
  });

  it("keeps reporting the fill on a partial one", () => {
    const figures = orderFigures(
      order({ filled_quantity: 100, filled_price: 184.2, status: "PARTIAL" }),
    );

    // The offered 300 is no longer the interesting number once 100 traded.
    expect(figures).toEqual({ quantity: 100, price: 184.2, filled: true });
  });

  it("still says what a cancelled order had offered", () => {
    const figures = orderFigures(order({ status: "CANCELLED" }));

    expect(figures).toEqual({ quantity: 300, price: 185, filled: false });
  });

  it("reports no price rather than zero when the provider gave none", () => {
    const figures = orderFigures(order({ order_price: null }));

    expect(figures.price).toBeNull();
  });
});
