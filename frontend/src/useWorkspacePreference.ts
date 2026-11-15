import { useEffect, useState } from "react";

export function useWorkspacePreference<T extends string>(
  key: string,
  fallback: T,
  allowed?: readonly T[],
) {
  const storageKey = `tradeforge.workspace.${key}`;
  const [value, setValue] = useState<T>(() => {
    try {
      const saved = localStorage.getItem(storageKey) as T | null;
      if (saved && (allowed ? allowed.includes(saved) : saved.length <= 64)) return saved;
    } catch {
      // The workspace also works when browser storage is unavailable.
    }
    return fallback;
  });

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, value);
    } catch {
      // Keep the current selection in memory when persistence is unavailable.
    }
  }, [storageKey, value]);

  return [value, setValue] as const;
}
