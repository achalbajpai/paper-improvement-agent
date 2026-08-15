"use client";

import { useRef } from "react";

import { cn } from "@/lib/cn";

export interface TabDefinition<T extends string = string> {
  id: T;
  label: string;
  count?: number;
}

export function Tabs<T extends string>({
  tabs,
  active,
  onSelect,
  label,
  className,
}: {
  tabs: TabDefinition<T>[];
  active: T;
  onSelect: (id: T) => void;
  label: string;
  className?: string;
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  const move = (from: number, delta: number) => {
    const next = tabs[(from + delta + tabs.length) % tabs.length];
    if (!next) return;
    onSelect(next.id);
    refs.current[next.id]?.focus();
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    const keys: Record<string, () => void> = {
      ArrowRight: () => move(index, 1),
      ArrowLeft: () => move(index, -1),
      Home: () => move(0, 0),
      End: () => move(tabs.length - 1, 0),
    };
    const handler = keys[event.key];
    if (!handler) return;
    event.preventDefault();
    handler();
  };

  return (
    <div
      role="tablist"
      aria-label={label}
      className={cn("-mx-1 flex items-center gap-1 overflow-x-auto px-1", className)}
    >
      {tabs.map((tab, index) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            ref={(node) => {
              refs.current[tab.id] = node;
            }}
            type="button"
            role="tab"
            id={tabId(tab.id)}
            aria-selected={selected}
            aria-controls={panelId(tab.id)}
            tabIndex={selected ? 0 : -1}
            onClick={() => onSelect(tab.id)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={cn(
              "inline-flex h-8 shrink-0 items-center gap-2 rounded-control px-3",
              "text-secondary font-medium transition-colors duration-state",
              selected
                ? "bg-surface-alt text-text"
                : "text-text-muted hover:bg-surface-alt hover:text-text",
            )}
          >
            {tab.label}
            {tab.count === undefined ? null : (
              <span
                className={cn(
                  "tabular rounded-control px-1 text-label",
                  selected ? "text-text-muted" : "text-text-muted",
                )}
              >
                {tab.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({
  id,
  active,
  children,
}: {
  id: string;
  active: string;
  children: React.ReactNode;
}) {
  const selected = id === active;
  return (
    <div
      role="tabpanel"
      id={panelId(id)}
      aria-labelledby={tabId(id)}
      hidden={!selected}
      tabIndex={selected ? 0 : -1}
    >
      {children}
    </div>
  );
}

function tabId(id: string) {
  return `tab-${id}`;
}

function panelId(id: string) {
  return `tabpanel-${id}`;
}
