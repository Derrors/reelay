import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  // Whitespace-nowrap: Badges should never wrap.
  // hover-elevate: adds subtle dark overlay on hover for interactive feedback
  "whitespace-nowrap inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold hover-elevate",
  {
    variants: {
      variant: {
        default:
          // Primary badge with subtle shadow for elevation
          "border-transparent bg-primary text-primary-foreground shadow-xs",
        secondary:
          // Subtle filled badge for secondary information
          "border-transparent bg-secondary text-secondary-foreground",
        destructive:
          // Error/danger state with matching shadow
          "border-transparent bg-destructive text-destructive-foreground shadow-xs",
        // Outline uses badge-outline variable to blend with parent background
        outline: "border [border-color:var(--badge-outline)] shadow-xs",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  );
}

export { Badge, badgeVariants }
