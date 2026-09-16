import type { getAccountDashboard } from "@/lib/api";

type Order = Awaited<ReturnType<typeof getAccountDashboard>>["orders"][number];

/**
 * What a row of the order table should actually show.
 *
 * A resting order has no fill, so the filled columns read 0 and `--` — which
 * on 2026-09-16 was every row on screen. The number that matters before a
 * fill is the one you offered; after a fill it is the one you got. Both live
 * in the same two columns, and the status column beside them says which.
 *
 * Quantity and price are decided together on purpose: showing an order price
 * next to a filled quantity of 0 reads as "zero shares at 185", which is
 * worse than showing nothing.
 */
export function orderFigures(order: Order): {
  quantity: number;
  price: number | null;
  filled: boolean;
} {
  const filled = order.filled_quantity > 0;
  return {
    quantity: filled ? order.filled_quantity : order.quantity,
    price: (filled ? order.filled_price : order.order_price) ?? null,
    filled,
  };
}
