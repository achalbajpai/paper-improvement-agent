"use client";

import { useRef, useState } from "react";

import { cn } from "@/lib/cn";

export function Dropzone({
  onFile,
  disabled = false,
  accept = "application/pdf",
  label = "Drop PDFs here or",
  browseLabel = "browse",
}: {
  onFile: (file: File) => void;
  disabled?: boolean;
  accept?: string;
  label?: string;
  browseLabel?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  const open = () => {
    if (!disabled) input.current?.click();
  };

  const take = (files: FileList | null) => {
    const file = files?.[0];
    if (file && !disabled) onFile(file);
  };

  return (
    <div
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-disabled={disabled}
      aria-label={`${label} ${browseLabel}`}
      onClick={open}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          open();
        }
      }}
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        take(event.dataTransfer.files);
      }}
      className={cn(
        "flex min-h-[220px] w-full cursor-pointer flex-col items-center justify-center gap-3",
        "rounded-card border-2 border-dashed transition-colors duration-state",
        over ? "border-accent bg-accent-soft" : "border-border hover:border-border-strong",
        disabled ? "cursor-not-allowed opacity-45" : undefined,
      )}
    >
      <input
        ref={input}
        type="file"
        accept={accept}
        className="hidden"
        disabled={disabled}
        onChange={(event) => {
          take(event.target.files);
          event.target.value = "";
        }}
      />

      <svg
        width="44"
        height="44"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        className="text-text-muted"
      >
        <path d="M13.234 20.252 21 12.3" />
        <path d="m16 6-8.414 8.586a2 2 0 0 0 0 2.828 2 2 0 0 0 2.828 0l8.414-8.586a4 4 0 0 0 0-5.656 4 4 0 0 0-5.656 0l-8.415 8.585a6 6 0 1 0 8.486 8.486" />
      </svg>

      <p className="text-card-title">
        {label} <span className="text-accent underline underline-offset-2">{browseLabel}</span>
      </p>
    </div>
  );
}
