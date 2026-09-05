"use client";

import { useState } from "react";
import { Eye, EyeOff } from "lucide-react";

import { maskPhone } from "@/lib/format";

/** Per-row reveal rather than a page-level toggle: revealing one number to
 * check a specific candidate should not put every other number on screen. */
export function Phone({ value }: { value: string }) {
  const [shown, setShown] = useState(false);
  const Icon = shown ? EyeOff : Eye;

  return (
    <span className="inline-flex items-center gap-1.5 font-mono text-xs">
      {shown ? value : maskPhone(value)}
      <button
        type="button"
        onClick={() => setShown((s) => !s)}
        aria-label={shown ? "Hide phone number" : "Reveal phone number"}
        className="text-muted-foreground hover:text-foreground"
      >
        <Icon className="size-3.5" aria-hidden />
      </button>
    </span>
  );
}
