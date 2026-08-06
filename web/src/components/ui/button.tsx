import { LoaderCircle } from "lucide-react";
import { forwardRef, type ComponentPropsWithoutRef } from "react";

import { cn } from "@/lib";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md" | "icon";

export type ButtonProps = ComponentPropsWithoutRef<"button"> & {
  loading?: boolean;
  size?: ButtonSize;
  variant?: ButtonVariant;
};

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    "border-transparent bg-primary text-primary-foreground shadow-sm hover:bg-primary-hover",
  secondary:
    "border-border bg-surface text-foreground shadow-sm hover:border-border-strong hover:bg-surface-muted",
  ghost:
    "border-transparent bg-transparent text-muted-foreground hover:bg-surface-muted hover:text-foreground",
  danger:
    "border-transparent bg-danger text-white shadow-sm hover:bg-danger/90",
};

const sizeClasses: Record<ButtonSize, string> = {
  sm: "h-8 gap-1.5 rounded-sm px-3 text-xs",
  md: "h-10 gap-2 rounded-md px-4 text-sm",
  icon: "size-9 rounded-md",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      children,
      className,
      disabled,
      loading = false,
      size = "md",
      type = "button",
      variant = "primary",
      ...props
    },
    ref,
  ) {
    return (
      <button
        aria-busy={loading}
        className={cn(
          "inline-flex shrink-0 cursor-pointer items-center justify-center border font-medium transition-colors duration-[var(--transition-fast)] disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
          variantClasses[variant],
          sizeClasses[size],
          className,
        )}
        disabled={disabled || loading}
        ref={ref}
        type={type}
        {...props}
      >
        {loading ? (
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
        ) : null}
        {children}
      </button>
    );
  },
);
