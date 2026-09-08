import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DoorOpenIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { awayModeKeys } from "@/features/dashboard/query-keys";
import { getAwayMode, setAwayMode } from "@/lib/api";
import { getErrorMessage } from "@/lib/format";

/** Switch that lets Aniu act on its own while you are not at the machine. */
export function AwayModeToggle() {
  const queryClient = useQueryClient();
  const stateQuery = useQuery({
    queryKey: awayModeKeys.state(),
    queryFn: getAwayMode,
    // Away mode lapses at midnight on its own, so unlike the rest of the
    // dashboard this value goes stale with nobody having acted on it. A page
    // left open overnight has to re-read it, which is exactly when the window
    // regains focus.
    refetchOnWindowFocus: true,
  });
  const enabled = stateQuery.data?.enabled ?? false;

  const toggle = useMutation({
    mutationFn: setAwayMode,
    onSuccess: (state) => {
      queryClient.setQueryData(awayModeKeys.state(), state);
      toast.success(
        state.enabled
          ? "已开启离开模式：运行结束后自动发送报告邮件，午夜自动关闭"
          : "已关闭离开模式",
      );
    },
    onError: (error: unknown) => toast.error(getErrorMessage(error)),
  });

  return (
    <Button
      type="button"
      // The theme's red token, used here for how loud it is rather than
      // because the action is destructive: away mode leaves Aniu acting on
      // its own, which should be obvious at a glance and easy to notice you
      // left on.
      variant={enabled ? "destructive" : "outline"}
      size="sm"
      role="switch"
      aria-checked={enabled}
      aria-label="离开模式"
      title={
        enabled
          ? "离开模式已开启：运行结束自动发送报告邮件，午夜自动关闭"
          : "开启后，任务运行结束会自动把报告发到你的邮箱"
      }
      disabled={stateQuery.isPending || toggle.isPending}
      onClick={() => toggle.mutate(!enabled)}
    >
      <DoorOpenIcon className="size-4" />
      {enabled ? "离开中" : "离开模式"}
    </Button>
  );
}
