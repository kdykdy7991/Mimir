import {
  Ban,
  Check,
  CircleAlert,
  CircleDashed,
  Clock3,
  LoaderCircle,
  Trash2,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib";

type StatusTone = "neutral" | "info" | "success" | "warning" | "danger";

type StatusDefinition = {
  icon: LucideIcon;
  label: string;
  pulse?: boolean;
  tone: StatusTone;
};

const statusDefinitions: Record<string, StatusDefinition> = {
  pending: { icon: Clock3, label: "等待中", tone: "neutral" },
  processing: {
    icon: LoaderCircle,
    label: "处理中",
    pulse: true,
    tone: "info",
  },
  running: {
    icon: LoaderCircle,
    label: "运行中",
    pulse: true,
    tone: "info",
  },
  ready: { icon: Check, label: "已就绪", tone: "success" },
  succeeded: { icon: Check, label: "已完成", tone: "success" },
  ok: { icon: Check, label: "正常", tone: "success" },
  degraded: { icon: TriangleAlert, label: "降级", tone: "warning" },
  failed: { icon: CircleAlert, label: "失败", tone: "danger" },
  down: { icon: CircleAlert, label: "不可用", tone: "danger" },
  deleting: { icon: Trash2, label: "删除中", tone: "warning" },
  cancelled: { icon: Ban, label: "已取消", tone: "neutral" },
};

const toneClasses: Record<StatusTone, string> = {
  neutral: "border-border bg-surface-muted text-muted-foreground",
  info: "border-info/20 bg-info/10 text-info",
  success: "border-success/20 bg-success/10 text-success",
  warning: "border-warning/20 bg-warning/10 text-warning",
  danger: "border-danger/20 bg-danger/10 text-danger",
};

export function StatusBadge({
  className,
  label,
  status,
}: {
  className?: string;
  label?: string;
  status: string;
}) {
  const definition = statusDefinitions[status] ?? {
    icon: CircleDashed,
    label: status,
    tone: "neutral" as const,
  };
  const Icon = definition.icon;

  return (
    <span
      className={cn(
        "inline-flex h-6 items-center gap-1.5 rounded-full border px-2.5 text-[0.6875rem] font-semibold",
        toneClasses[definition.tone],
        className,
      )}
    >
      <Icon
        aria-hidden="true"
        className={cn("size-3", definition.pulse && "animate-spin")}
      />
      {label ?? definition.label}
    </span>
  );
}
