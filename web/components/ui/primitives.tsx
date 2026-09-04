/**
 * The four primitives, written against Grace's palette.
 *
 * **Not what the shadcn CLI produced, and three measured reasons why.** Running
 * `npx shadcn@4.20.1 add button card badge table --yes` in this project writes
 * the `base-nova` style, and its output:
 *
 * 1. **imports `@base-ui/react`, which is not installed** — `@base-ui/react/button`,
 *    `/merge-props`, and `/use-render` all fail with `TS2307`. Installing it is a
 *    new runtime dependency for four components used at four call sites, against
 *    a project whose dependency rule has held since Plan 1 Task 1.
 * 2. **did not write `lib/utils.ts`**, which all four of its own files import.
 * 3. **is styled entirely in tokens Grace's palette does not define** —
 *    `bg-primary`, `text-primary-foreground`, `border-ring`, `bg-destructive`,
 *    `bg-secondary`, `bg-background`, `text-muted-foreground`, `border-border`.
 *    Measured rather than assumed: a probe page using them was built, and
 *    Tailwind 4 emitted **no rule at all** for any of them (`bg-primary -> files=0`)
 *    while `bg-paper`, `text-ink`, `border-rule`, and `text-escalate` each emitted
 *    `.bg-paper{background-color:var(--color-paper)}` and so on. Tailwind 4
 *    generates a utility only for a token present in `@theme`, so the CLI's
 *    components would have rendered **unstyled** — invisible white-on-white
 *    buttons, on a page that builds, lints, and typechecks clean. The plan's
 *    "decline the globals.css overwrite" instruction protects the palette and
 *    leaves this half unaddressed.
 *
 * So these are hand-written from the same idea (a `cva` variant table plus `cn`),
 * in the palette that exists. `class-variance-authority`, `clsx`, and
 * `tailwind-merge` were pinned in Task 1 and are all that is used.
 *
 * `Badge` and `Table` carry no interactive behaviour, so they are plain function
 * components rather than `React.forwardRef` — Next 16 runs React 19, where a
 * function component receives `ref` as an ordinary prop.
 */

import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/* ---------------------------------------------------------------- Card ---- */

/** A framed region. `rounded-none` throughout: the palette is administrative
 *  and a form on a benefits queue should read like a form, not a marketing
 *  card. */
export function Card({ className, ...props }: React.ComponentProps<"section">) {
  return (
    <section
      className={cn("border border-rule bg-paper", className)}
      {...props}
    />
  );
}

export function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("border-b border-rule px-5 py-4", className)} {...props} />;
}

export function CardTitle({ className, ...props }: React.ComponentProps<"h2">) {
  return (
    <h2
      className={cn("text-[0.9375rem] font-semibold tracking-tight text-ink", className)}
      {...props}
    />
  );
}

export function CardBody({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("px-5 py-4", className)} {...props} />;
}

/* -------------------------------------------------------------- Button ---- */

const buttonVariants = cva(
  // `focus-visible` is not optional: a caseworker deciding a case by keyboard
  // must be able to see which control is focused.
  "inline-flex items-center justify-center rounded-none border px-3.5 py-2 " +
    "text-sm font-medium transition-colors outline-none " +
    "focus-visible:ring-2 focus-visible:ring-ink focus-visible:ring-offset-2 " +
    "focus-visible:ring-offset-paper disabled:pointer-events-none disabled:opacity-40",
  {
    variants: {
      variant: {
        // Approve is the affirmative action, so it carries the acted colour —
        // the same green the sweep summary uses for "handled alone".
        primary: "border-acted bg-acted text-paper hover:bg-ink hover:border-ink",
        // Deny keeps a case escalated, which is the cautious outcome. It reads
        // as the quieter of the two on purpose: nothing about keeping a family
        // in the queue should look like the default next step.
        secondary: "border-rule bg-paper text-ink hover:border-ink",
      },
    },
    defaultVariants: { variant: "primary" },
  },
);

export type ButtonProps = React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants>;

export function Button({ className, variant, ...props }: ButtonProps) {
  return <button className={cn(buttonVariants({ variant }), className)} {...props} />;
}

/* --------------------------------------------------------------- Badge ---- */

const badgeVariants = cva(
  "inline-flex items-center rounded-none border px-2 py-0.5 font-mono text-[0.6875rem] " +
    "uppercase tracking-[0.08em]",
  {
    variants: {
      tone: {
        // Escalate is the only saturated fill in the application. A caseworker
        // scans for work, and the work is the escalations.
        escalate: "border-escalate bg-escalate text-paper",
        acted: "border-acted text-acted",
        error: "border-error text-error",
        neutral: "border-rule text-muted",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export type BadgeProps = React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>;

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

/* --------------------------------------------------------------- Table ---- */

export function Table({ className, ...props }: React.ComponentProps<"table">) {
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn("w-full border-collapse text-left text-sm", className)} {...props} />
    </div>
  );
}

export function TableHead({ className, ...props }: React.ComponentProps<"thead">) {
  return (
    <thead
      className={cn(
        "border-b border-ink font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-muted",
        className,
      )}
      {...props}
    />
  );
}

export function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return <tbody className={className} {...props} />;
}

export function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return <tr className={cn("border-b border-rule align-baseline", className)} {...props} />;
}

export function TableHeaderCell({ className, ...props }: React.ComponentProps<"th">) {
  return <th className={cn("px-3 py-2 font-normal first:pl-0 last:pr-0", className)} {...props} />;
}

export function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return <td className={cn("px-3 py-3 first:pl-0 last:pr-0", className)} {...props} />;
}
