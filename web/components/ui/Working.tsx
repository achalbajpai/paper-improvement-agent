"use client";

import { useEffect, useState } from "react";

import { cn } from "@/lib/cn";

function useElapsed() {
  const [tenths, setTenths] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => setTenths((value) => value + 1), 100);
    return () => clearInterval(timer);
  }, []);

  const seconds = tenths / 10;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m ${(seconds % 60).toFixed(0)}s`;
}

const DELAYS = Array.from({ length: 9 }, (_, index) => {
  const row = Math.floor(index / 3);
  const column = index % 3;
  return (column + Math.abs(row - 1)) * 90;
});

export function Working({ label, className }: { label: string; className?: string }) {
  const elapsed = useElapsed();

  return (
    <p className={cn("flex w-fit items-center gap-3", className)} role="status">
      <span aria-hidden className="grid grid-cols-[repeat(3,4px)] gap-0.5">
        {DELAYS.map((delay, index) => (
          <span
            key={index}
            className="size-1 rounded-[1px] bg-text"
            style={{ opacity: 0.15, animation: `pixel-on 650ms ease-in-out ${delay}ms infinite` }}
          />
        ))}
      </span>
      <span className="text-secondary text-text">{label}</span>
      <span className="tabular font-mono text-label text-text-muted">{elapsed}</span>
    </p>
  );
}
