import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRingIcon, PlusIcon, SendIcon, Trash2Icon } from "lucide-react";
import { toast } from "sonner";

import { QueryErrorState, QueryLoadingState } from "@/components/query-state";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { SecretInput } from "@/components/ui/secret-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { notificationKeys } from "@/features/settings/query-keys";
import {
  createNotificationChannel,
  deleteNotificationChannel,
  listNotificationChannels,
  testNotificationChannel,
  updateNotificationChannel,
} from "@/lib/api";
import type {
  NotificationChannel,
  NotificationChannelKind,
  TradeNotificationEvent,
} from "@/lib/api-types";
import { getErrorMessage } from "@/lib/format";

const KIND_LABELS: Record<NotificationChannelKind, string> = {
  webhook: "通用 Webhook",
  serverchan: "Server酱",
  wecom_bot: "企业微信机器人",
};

/** What the user must paste for each transport, and how it is used. */
const SECRET_HINTS: Record<NotificationChannelKind, string> = {
  webhook: "完整的接收地址，例如 https://open.feishu.cn/open-apis/bot/v2/hook/xxxx",
  serverchan: "Server酱 SendKey（也可直接粘贴完整推送地址）",
  wecom_bot: "群机器人 Webhook 的 key 参数（也可直接粘贴完整地址）",
};

const EVENTS: { id: TradeNotificationEvent; label: string; description: string }[] = [
  {
    id: "order_placed",
    label: "下单",
    description: "智能体提交限价委托且被模拟盘受理",
  },
  {
    id: "order_cancelled",
    label: "撤单",
    description: "智能体撤销单笔委托或一键撤单成功",
  },
  {
    id: "order_filled",
    label: "成交",
    description: "账户刷新时发现委托有新增成交量（含部分成交）",
  },
];

const DEFAULT_EVENTS: TradeNotificationEvent[] = [
  "order_placed",
  "order_cancelled",
  "order_filled",
];

const TEMPLATE_PLACEHOLDER = `留空则推送完整事件 JSON。也可自定义，例如飞书：
{"msg_type":"text","content":{"text":"{{title}}\\n{{text}}"}}`;

function eventLabel(id: TradeNotificationEvent) {
  return EVENTS.find((event) => event.id === id)?.label ?? id;
}

type ChannelDraft = {
  name: string;
  kind: NotificationChannelKind;
  secret: string;
  events: TradeNotificationEvent[];
  bodyTemplate: string;
};

function emptyDraft(): ChannelDraft {
  return {
    name: "",
    kind: "webhook",
    secret: "",
    events: [...DEFAULT_EVENTS],
    bodyTemplate: "",
  };
}

function EventCheckboxes({
  selected,
  disabled,
  idPrefix,
  onToggle,
}: {
  selected: TradeNotificationEvent[];
  disabled?: boolean;
  idPrefix: string;
  onToggle: (id: TradeNotificationEvent, checked: boolean) => void;
}) {
  return (
    <div className="space-y-2">
      {EVENTS.map((event) => (
        <label
          key={event.id}
          htmlFor={`${idPrefix}-${event.id}`}
          className="flex items-start gap-2.5"
        >
          <Checkbox
            id={`${idPrefix}-${event.id}`}
            checked={selected.includes(event.id)}
            disabled={disabled}
            onCheckedChange={(checked) => onToggle(event.id, checked === true)}
            className="mt-0.5"
          />
          <span className="min-w-0">
            <span className="text-sm font-medium">{event.label}</span>
            <span className="text-muted-foreground block text-xs">{event.description}</span>
          </span>
        </label>
      ))}
    </div>
  );
}

function CreateChannelDialog({ disabled }: { disabled: boolean }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<ChannelDraft>(emptyDraft);

  const createMutation = useMutation({
    mutationFn: createNotificationChannel,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: notificationKeys.channels });
      setDraft(emptyDraft());
      setOpen(false);
      toast.success("推送通道已创建");
    },
    onError: (error: unknown) => toast.error(getErrorMessage(error)),
  });

  const canSubmit =
    draft.name.trim().length > 0 && draft.secret.trim().length > 0 && draft.events.length > 0;

  const submit = () => {
    if (!canSubmit) return;
    createMutation.mutate({
      name: draft.name.trim(),
      kind: draft.kind,
      secret: draft.secret.trim(),
      enabled: true,
      subscribed_events: draft.events,
      body_template:
        draft.kind === "webhook" && draft.bodyTemplate.trim() ? draft.bodyTemplate.trim() : null,
    });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setDraft(emptyDraft());
      }}
    >
      <DialogTrigger asChild>
        <Button type="button" disabled={disabled}>
          <PlusIcon className="size-4" />
          新增通道
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>新增推送通道</DialogTitle>
          <DialogDescription>
            地址和密钥加密保存在本地，保存后不会再回显，只显示脱敏提示。
          </DialogDescription>
        </DialogHeader>

        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <Field>
            <FieldLabel htmlFor="notification-name">通道名称</FieldLabel>
            <Input
              id="notification-name"
              value={draft.name}
              maxLength={64}
              autoComplete="off"
              placeholder="例如：我的手机"
              onChange={(event) => setDraft({ ...draft, name: event.target.value })}
            />
          </Field>

          <Field>
            <FieldLabel htmlFor="notification-kind">通道类型</FieldLabel>
            <Select
              value={draft.kind}
              onValueChange={(value) =>
                setDraft({ ...draft, kind: value as NotificationChannelKind })
              }
            >
              <SelectTrigger id="notification-kind" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(KIND_LABELS) as NotificationChannelKind[]).map((kind) => (
                  <SelectItem key={kind} value={kind}>
                    {KIND_LABELS[kind]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field>
            <FieldLabel htmlFor="notification-secret">地址 / 密钥</FieldLabel>
            <SecretInput
              id="notification-secret"
              value={draft.secret}
              autoComplete="off"
              onChange={(event) => setDraft({ ...draft, secret: event.target.value })}
            />
            <FieldDescription>{SECRET_HINTS[draft.kind]}</FieldDescription>
          </Field>

          <Field>
            <FieldLabel>推送事件</FieldLabel>
            <EventCheckboxes
              idPrefix="notification-create"
              selected={draft.events}
              onToggle={(id, checked) =>
                setDraft({
                  ...draft,
                  events: checked
                    ? [...draft.events, id]
                    : draft.events.filter((item) => item !== id),
                })
              }
            />
          </Field>

          {draft.kind === "webhook" ? (
            <Field>
              <FieldLabel htmlFor="notification-template">消息体模板（可选）</FieldLabel>
              <Textarea
                id="notification-template"
                rows={4}
                value={draft.bodyTemplate}
                placeholder={TEMPLATE_PLACEHOLDER}
                className="font-mono text-xs"
                onChange={(event) => setDraft({ ...draft, bodyTemplate: event.target.value })}
              />
              <FieldDescription>
                用 {"{{title}}"}、{"{{text}}"}、{"{{stock_code}}"}、{"{{order_id}}"}{" "}
                等占位符引用事件字段。
              </FieldDescription>
            </Field>
          ) : null}

          <DialogFooter>
            <Button type="submit" disabled={!canSubmit || createMutation.isPending}>
              创建通道
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ChannelRow({ channel }: { channel: NotificationChannel }) {
  const queryClient = useQueryClient();

  const invalidate = () =>
    void queryClient.invalidateQueries({ queryKey: notificationKeys.channels });
  const reportError = (error: unknown) => toast.error(getErrorMessage(error));

  const updateMutation = useMutation({
    mutationFn: (payload: Parameters<typeof updateNotificationChannel>[1]) =>
      updateNotificationChannel(channel.id, payload),
    onSuccess: invalidate,
    onError: reportError,
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteNotificationChannel(channel.id),
    onSuccess: () => {
      invalidate();
      toast.success(`已删除通道「${channel.name}」`);
    },
    onError: reportError,
  });

  const testMutation = useMutation({
    mutationFn: () => testNotificationChannel(channel.id),
    onSuccess: (result) => {
      if (result.delivered) {
        toast.success(result.message);
      } else {
        toast.error(result.message);
      }
    },
    onError: reportError,
  });

  const busy = updateMutation.isPending || deleteMutation.isPending || testMutation.isPending;

  return (
    <li className="rounded-md border p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">{channel.name}</span>
            <Badge variant="outline" className="h-5 rounded-sm px-1.5 text-[11px] leading-none">
              {KIND_LABELS[channel.kind]}
            </Badge>
          </div>
          <p className="text-muted-foreground font-mono text-xs break-all">{channel.target_hint}</p>
          <div className="flex flex-wrap gap-1">
            {channel.subscribed_events.map((event) => (
              <Badge
                key={event}
                variant="secondary"
                className="h-5 rounded-sm px-1.5 text-[11px] leading-none"
              >
                {eventLabel(event)}
              </Badge>
            ))}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <Switch
            checked={channel.enabled}
            disabled={busy}
            aria-label={`启用通道 ${channel.name}`}
            onCheckedChange={(checked) => updateMutation.mutate({ enabled: checked })}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => testMutation.mutate()}
          >
            <SendIcon className="size-4" />
            测试
          </Button>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button type="button" variant="outline" size="sm" disabled={busy}>
                <Trash2Icon className="size-4" />
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>删除推送通道</AlertDialogTitle>
                <AlertDialogDescription>
                  删除「{channel.name}」后，其地址和密钥会一并从本地移除，无法恢复。
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>取消</AlertDialogCancel>
                <AlertDialogAction onClick={() => deleteMutation.mutate()}>删除</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>

      <div className="mt-3 border-t pt-3">
        <EventCheckboxes
          idPrefix={`notification-${channel.id}`}
          selected={channel.subscribed_events}
          disabled={busy}
          onToggle={(id, checked) => {
            const next = checked
              ? [...channel.subscribed_events, id]
              : channel.subscribed_events.filter((item) => item !== id);
            if (next.length === 0) {
              toast.error("至少需要保留一个推送事件");
              return;
            }
            updateMutation.mutate({ subscribed_events: next });
          }}
        />
      </div>
    </li>
  );
}

/** Manage where trade lifecycle events get pushed. */
export function NotificationsSettingsPage() {
  const channelsQuery = useQuery({
    queryKey: notificationKeys.channels,
    queryFn: listNotificationChannels,
  });
  const channels = channelsQuery.data;

  if (channelsQuery.isLoading) {
    return <QueryLoadingState label="正在加载推送通道…" />;
  }
  if (channelsQuery.isError && !channels) {
    return (
      <QueryErrorState
        title="推送通道加载失败"
        error={channelsQuery.error}
        onRetry={() => void channelsQuery.refetch()}
      />
    );
  }
  if (!channels) return null;

  return (
    <section className="w-full max-w-[986px] space-y-4" aria-label="推送通知设置内容">
      {channelsQuery.error ? (
        <p className="text-destructive text-sm">
          后台刷新失败：{getErrorMessage(channelsQuery.error)}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-muted-foreground text-sm">
          成交事件依赖账户刷新，因此会比下单和撤单晚一个刷新周期。
        </p>
        <CreateChannelDialog disabled={channelsQuery.isFetching} />
      </div>

      {channels.length === 0 ? (
        <Empty>
          <EmptyMedia variant="icon">
            <BellRingIcon />
          </EmptyMedia>
          <EmptyTitle>还没有推送通道</EmptyTitle>
          <EmptyDescription>
            添加一个通道后，智能体下单、撤单和成交时会推送到你的手机或群聊。
          </EmptyDescription>
        </Empty>
      ) : (
        <ul className="space-y-3">
          {channels.map((channel) => (
            <ChannelRow key={channel.id} channel={channel} />
          ))}
        </ul>
      )}
    </section>
  );
}
