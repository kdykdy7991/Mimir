// Tag colour tokens — single source of truth for the knowledge-base tag palette.
//
// The backend validates every submitted colour against
// `TAG_COLORS = frozenset({"grey", "blue", "green", "red", "purple", "amber"})`
// (see src/application/services/web_store.py:44). Sending any other token —
// including the common "gray" spelling — is rejected by the API. The list
// below is the only palette the UI should render or accept from existing data.

export const TAG_COLORS = [
  "grey",
  "blue",
  "green",
  "amber",
  "red",
  "purple",
] as const;

export type TagColor = (typeof TAG_COLORS)[number];

export function isTagColor(value: unknown): value is TagColor {
  return typeof value === "string" && (TAG_COLORS as readonly string[]).includes(value);
}

const PILL_CLASSES: Record<TagColor, string> = {
  grey: "bg-slate-100 text-slate-700 dark:bg-slate-700/40 dark:text-slate-200",
  blue: "bg-blue-100 text-blue-700 dark:bg-blue-700/40 dark:text-blue-200",
  green:
    "bg-emerald-100 text-emerald-700 dark:bg-emerald-700/40 dark:text-emerald-200",
  amber:
    "bg-amber-100 text-amber-700 dark:bg-amber-700/40 dark:text-amber-200",
  red: "bg-rose-100 text-rose-700 dark:bg-rose-700/40 dark:text-rose-200",
  purple:
    "bg-violet-100 text-violet-700 dark:bg-violet-700/40 dark:text-violet-200",
};

const DOT_CLASSES: Record<TagColor, string> = {
  grey: "bg-slate-400",
  blue: "bg-blue-500",
  green: "bg-emerald-500",
  amber: "bg-amber-500",
  red: "bg-rose-500",
  purple: "bg-violet-500",
};

export function tagPillClass(color: string | undefined | null): string {
  if (isTagColor(color)) return PILL_CLASSES[color];
  return PILL_CLASSES.grey;
}

export function tagDotClass(color: string | undefined | null): string {
  if (isTagColor(color)) return DOT_CLASSES[color];
  return DOT_CLASSES.grey;
}