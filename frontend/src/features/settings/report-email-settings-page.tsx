import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2Icon, CircleDashedIcon, SaveIcon } from "lucide-react";
import { toast } from "sonner";

import { QueryErrorState, QueryLoadingState } from "@/components/query-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { SecretInput } from "@/components/ui/secret-input";
import { Switch } from "@/components/ui/switch";
import { reportEmailKeys } from "@/features/settings/query-keys";
import { getReportEmailSettings, saveReportEmailSettings } from "@/lib/api";
import { getErrorMessage } from "@/lib/format";
import { cn } from "@/lib/utils";

function StatusBadge({ configured }: { configured: boolean }) {
  return (
    <Badge
      variant="outline"
      className={cn(
        "h-5 gap-1 rounded-sm px-1.5 text-[11px] leading-none font-medium",
        configured
          ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-700"
          : "border-amber-500/30 bg-amber-500/10 text-amber-700",
      )}
    >
      {configured ? (
        <CheckCircle2Icon className="size-3" />
      ) : (
        <CircleDashedIcon className="size-3" />
      )}
      {configured ? "已配置" : "未配置"}
    </Badge>
  );
}

/** Configure where run reports are mailed. */
export function ReportEmailSettingsPage() {
  const queryClient = useQueryClient();
  const [sender, setSender] = useState<string | null>(null);
  const [recipient, setRecipient] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");

  const settingsQuery = useQuery({
    queryKey: reportEmailKeys.settings,
    queryFn: getReportEmailSettings,
  });
  const settings = settingsQuery.data;

  const saveMutation = useMutation({
    mutationFn: saveReportEmailSettings,
    onSuccess: (saved) => {
      queryClient.setQueryData(reportEmailKeys.settings, saved);
      setSender(null);
      setRecipient(null);
      setApiKey("");
      toast.success("报告邮件设置已保存");
    },
    onError: (error: unknown) => toast.error(getErrorMessage(error)),
  });

  if (settingsQuery.isLoading) {
    return <QueryLoadingState label="正在加载报告邮件设置…" />;
  }
  if (settingsQuery.isError && !settings) {
    return (
      <QueryErrorState
        title="报告邮件设置加载失败"
        error={settingsQuery.error}
        onRetry={() => void settingsQuery.refetch()}
      />
    );
  }
  if (!settings) return null;

  const senderValue = sender ?? settings.sender;
  const recipientValue = recipient ?? settings.recipient;
  const canSave =
    senderValue.trim().length > 0 &&
    recipientValue.trim().length > 0 &&
    (settings.api_key_configured || apiKey.trim().length > 0) &&
    !saveMutation.isPending;

  const submit = () => {
    if (!canSave) return;
    saveMutation.mutate({
      sender: senderValue.trim(),
      recipient: recipientValue.trim(),
      ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
    });
  };

  return (
    <section className="w-full max-w-[986px] space-y-4" aria-label="报告邮件设置内容">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge configured={settings.configured && settings.api_key_configured} />
        {settings.api_key_last_four ? (
          <span className="text-muted-foreground text-xs">
            API Key 尾号 {settings.api_key_last_four}
          </span>
        ) : null}
        {settings.configured ? (
          <label className="ms-auto flex items-center gap-2 text-sm">
            <span>启用</span>
            <Switch
              checked={settings.enabled}
              aria-label="启用报告邮件"
              disabled={saveMutation.isPending}
              onCheckedChange={(checked) => saveMutation.mutate({ enabled: checked })}
            />
          </label>
        ) : null}
      </div>

      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Field>
          <FieldLabel htmlFor="report-email-key">Resend API Key</FieldLabel>
          <SecretInput
            id="report-email-key"
            value={apiKey}
            autoComplete="off"
            placeholder={settings.api_key_configured ? "留空保持当前密钥" : "re_..."}
            onChange={(event) => setApiKey(event.target.value)}
          />
          <FieldDescription>在 Resend 后台创建，加密保存在本地，保存后不再回显。</FieldDescription>
        </Field>

        <Field>
          <FieldLabel htmlFor="report-email-sender">发件地址</FieldLabel>
          <Input
            id="report-email-sender"
            value={senderValue}
            autoComplete="off"
            placeholder="onboarding@resend.dev"
            onChange={(event) => setSender(event.target.value)}
          />
          <FieldDescription>
            必须是 Resend
            已授权的地址。没有验证过自己的域名时，只能用它给出的测试发件地址，且收件人只能是你的注册邮箱。
          </FieldDescription>
        </Field>

        <Field>
          <FieldLabel htmlFor="report-email-recipient">收件地址</FieldLabel>
          <Input
            id="report-email-recipient"
            value={recipientValue}
            autoComplete="off"
            placeholder="me@example.com"
            onChange={(event) => setRecipient(event.target.value)}
          />
        </Field>

        <Button type="submit" disabled={!canSave}>
          <SaveIcon className="size-4" />
          保存设置
        </Button>
      </form>

      <p className="text-muted-foreground text-sm">
        配置好之后，到「任务运行」页面打开任意一次已完成的运行，报告标题旁会出现「发送到邮箱」按钮。
      </p>
    </section>
  );
}
