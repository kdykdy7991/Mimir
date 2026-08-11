export function calculateBatchProgress(
  total: number,
  settledWithoutTask: number,
  taskPercentages: readonly number[],
) {
  const completed = Math.min(
    total,
    settledWithoutTask + taskPercentages.filter((value) => value >= 100).length,
  );
  if (total <= 0) return { completed: 0, percent: 0 };
  const progress = settledWithoutTask * 100
    + taskPercentages.reduce((sum, value) => sum + Math.max(0, Math.min(100, value)), 0);
  return { completed, percent: Math.min(100, Math.round(progress / total)) };
}
