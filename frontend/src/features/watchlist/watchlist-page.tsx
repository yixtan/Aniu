import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PlusIcon, StarIcon, Trash2Icon } from "lucide-react";
import { toast } from "sonner";

import { QueryErrorState, QueryLoadingState } from "@/components/query-state";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { watchlistKeys } from "@/features/watchlist/query-keys";
import { addWatchlistItem, deleteWatchlistItem, listWatchlist } from "@/lib/api";
import { getErrorMessage } from "@/lib/format";

/** How many companies the backend will store; mirrored for the counter. */
const MAX_FOLLOWED = 10;

/** How long a company has been followed, at the coarsest useful scale. */
function followedFor(since: string): string {
  const started = new Date(since).getTime();
  if (Number.isNaN(started)) return "--";
  const days = Math.floor((Date.now() - started) / 86_400_000);
  if (days >= 365) {
    const years = Math.floor(days / 365);
    return `${years} 年${days % 365 >= 30 ? ` ${Math.floor((days % 365) / 30)} 个月` : ""}`;
  }
  if (days >= 30) return `${Math.floor(days / 30)} 个月`;
  if (days >= 1) return `${days} 天`;
  return "今天";
}

export function WatchlistPage() {
  const queryClient = useQueryClient();
  const [symbol, setSymbol] = useState("");

  const listQuery = useQuery({
    queryKey: watchlistKeys.list(),
    queryFn: listWatchlist,
  });
  const items = listQuery.data ?? [];

  const addMutation = useMutation({
    mutationFn: addWatchlistItem,
    onSuccess: (item) => {
      void queryClient.invalidateQueries({ queryKey: watchlistKeys.all });
      setSymbol("");
      toast.success(`已关注 ${item.name}`);
    },
    onError: (error: unknown) => toast.error(getErrorMessage(error)),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteWatchlistItem,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: watchlistKeys.all });
      toast.success("已移出关注清单");
    },
    onError: (error: unknown) => toast.error(getErrorMessage(error)),
  });

  if (listQuery.isLoading) {
    return <QueryLoadingState label="正在加载关注清单…" />;
  }
  if (listQuery.isError) {
    return (
      <QueryErrorState
        title="关注清单加载失败"
        error={listQuery.error}
        onRetry={() => void listQuery.refetch()}
      />
    );
  }

  const isFull = items.length >= MAX_FOLLOWED;
  const canAdd = symbol.trim().length > 0 && !isFull && !addMutation.isPending;

  return (
    <section className="w-full max-w-[986px] space-y-5" aria-label="关注股票内容">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (canAdd) addMutation.mutate(symbol.trim());
        }}
      >
        <Field className="max-w-xs flex-1">
          <FieldLabel htmlFor="watchlist-symbol">股票代码</FieldLabel>
          <Input
            id="watchlist-symbol"
            value={symbol}
            autoComplete="off"
            placeholder="600519"
            disabled={isFull}
            onChange={(event) => setSymbol(event.target.value)}
          />
          <FieldDescription>
            {isFull
              ? `已达上限 ${MAX_FOLLOWED} 只，请先移除不再关注的`
              : "只需填代码，保存时自动查询并记录名称"}
          </FieldDescription>
        </Field>
        <Button type="submit" disabled={!canAdd}>
          <PlusIcon className="size-4" />
          加入关注
        </Button>
        <span className="text-muted-foreground ms-auto text-sm tabular-nums">
          {items.length} / {MAX_FOLLOWED}
        </span>
      </form>

      {items.length === 0 ? (
        <Empty className="min-h-[220px] justify-center">
          <EmptyHeader>
            <EmptyTitle>还没有关注的股票</EmptyTitle>
            <EmptyDescription>
              加入你自己熟悉或感兴趣的公司。Aniu 每次运行会把它们纳入参考，
              但仍按自己的方法独立选股。
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[30%]">代码</TableHead>
              <TableHead className="w-[40%]">名称</TableHead>
              <TableHead className="w-[20%]">已关注</TableHead>
              <TableHead className="w-[10%] text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.id}>
                <TableCell className="font-medium tabular-nums">{item.symbol}</TableCell>
                <TableCell>
                  <span className="inline-flex items-center gap-1.5">
                    <StarIcon className="text-muted-foreground/60 size-3.5" />
                    {item.name}
                  </span>
                </TableCell>
                <TableCell className="text-muted-foreground tabular-nums">
                  {followedFor(item.created_at)}
                </TableCell>
                <TableCell className="text-right">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    title={`移出关注 ${item.name}`}
                    aria-label={`移出关注 ${item.name}`}
                    disabled={deleteMutation.isPending}
                    onClick={() => deleteMutation.mutate(item.id)}
                  >
                    <Trash2Icon className="text-destructive size-4" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  );
}
