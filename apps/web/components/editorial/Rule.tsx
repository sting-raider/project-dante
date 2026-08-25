import { cn } from "@/lib/cn";

type RuleProps = {
  /** "hairline" = 1px rule color; "ink" = heavy editorial bar; "double" = classic newspaper double rule; "signal" = red breach line. */
  weight?: "hairline" | "ink" | "double" | "signal";
  className?: string;
} & React.HTMLAttributes<HTMLHRElement>;

/**
 * Full-bleed-able hairline rule — the house separator (plan §27.5).
 * Render as <hr> so it is free for screen readers.
 */
export default function Rule({ weight = "hairline", className, ...rest }: RuleProps) {
  return (
    <hr
      aria-hidden={true}
      className={cn(
        "m-0 w-full border-t",
        weight === "hairline" && "border-rule",
        weight === "ink" && "border-ink border-t-[3px]",
        weight === "signal" && "border-signal border-t-[3px]",
        weight === "double" &&
          "border-0 border-t border-rule relative h-[4px] after:absolute after:inset-x-0 after:top-[3px] after:border-t after:border-rule",
        className
      )}
      {...rest}
    />
  );
}
