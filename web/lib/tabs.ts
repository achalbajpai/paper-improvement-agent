"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";

export function resolveTab<T extends string>(
  raw: string | null | undefined,
  allowed: readonly T[],
  fallback: T,
): T {
  if (!raw) return fallback;
  return allowed.includes(raw as T) ? (raw as T) : fallback;
}

export function useTabParam<T extends string>(
  allowed: readonly T[],
  fallback: T,
): {
  tab: T;
  anchor: string | null;
  select: (next: T) => void;
  selectWithAnchor: (next: T, anchor: string) => void;
} {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const tab = resolveTab(params.get("tab"), allowed, fallback);
  const anchor = params.get("anchor");

  const write = useCallback(
    (next: T, nextAnchor: string | null) => {
      const search = new URLSearchParams(params.toString());
      if (next === fallback) search.delete("tab");
      else search.set("tab", next);
      if (nextAnchor) search.set("anchor", nextAnchor);
      else search.delete("anchor");
      const query = search.toString();
      router.push(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [params, router, pathname, fallback],
  );

  return {
    tab,
    anchor,
    select: useCallback((next: T) => write(next, null), [write]),
    selectWithAnchor: useCallback(
      (next: T, next_anchor: string) => write(next, next_anchor),
      [write],
    ),
  };
}
