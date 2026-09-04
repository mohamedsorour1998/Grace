/**
 * `cn` — merge class names, last-wins on conflicting Tailwind utilities.
 *
 * The shadcn CLI is documented to write this file and **did not**: running
 * `npx shadcn@4.20.1 add button card badge table --yes` created the four
 * components and no `lib/utils.ts`, so all four failed to compile with
 * `TS2307: Cannot find module '@/lib/utils'`. Written by hand instead; `clsx`
 * and `tailwind-merge` were already pinned in Task 1 for exactly this.
 */

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
