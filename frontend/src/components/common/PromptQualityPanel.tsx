import type { PromptQualityIssue } from "@/lib/prompt-quality";

interface PromptQualityPanelProps {
  issues: PromptQualityIssue[];
  title?: string;
  compact?: boolean;
}

const severityStyles: Record<PromptQualityIssue["severity"], string> = {
  error: "border-red-500/30 bg-red-500/10 text-red-100",
  warning: "border-amber-500/30 bg-amber-500/10 text-amber-100",
  info: "border-sky-500/30 bg-sky-500/10 text-sky-100",
};

const severityLabel: Record<PromptQualityIssue["severity"], string> = {
  error: "阻断项",
  warning: "提醒",
  info: "建议",
};

export default function PromptQualityPanel({
  issues,
  title = "提示词质检器",
  compact = false,
}: PromptQualityPanelProps) {
  if (issues.length === 0) {
    return null;
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-400">
          {title}
        </p>
        <span className="text-[10px] text-gray-500">{issues.length} 项</span>
      </div>

      <div className="space-y-2">
        {issues.map((issue) => (
          <div
            key={`${issue.code}-${issue.title}`}
            className={`rounded-xl border px-3 py-2 ${severityStyles[issue.severity]}`}
          >
            <div className="flex items-center justify-between gap-3">
              <p className={`${compact ? "text-xs" : "text-sm"} font-medium`}>{issue.title}</p>
              <span className="text-[10px] uppercase tracking-[0.12em] opacity-80">
                {severityLabel[issue.severity]}
              </span>
            </div>
            <p className={`mt-1 ${compact ? "text-[11px]" : "text-xs"} leading-relaxed opacity-90`}>
              {issue.detail}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
