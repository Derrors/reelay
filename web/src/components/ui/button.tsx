import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

/** 按钮：主操作用蓝色实心；outline 是白底细线（最常用）；secondary 是灰底；ghost 只在悬停时出底色。
 *  桌面端紧凑（32px），移动端可点区域不低于 36px；按下时轻微收缩。 */
const buttonVariants = cva(
  "press inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-[13px] font-medium outline-none select-none focus-visible:ring-[3px] focus-visible:ring-ring/25 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground shadow-[0_1px_2px_oklch(0%_0_0/0.12),inset_0_1px_0_oklch(100%_0_0/0.14)] hover:bg-primary/90",
        destructive: "bg-destructive text-destructive-foreground shadow-[0_1px_2px_oklch(0%_0_0/0.12)] hover:bg-destructive/90",
        outline: "bg-card text-foreground shadow-btn hover:bg-accent",
        secondary: "bg-secondary text-secondary-foreground hover:bg-hover-2",
        ghost: "text-foreground/85 hover:bg-accent hover:text-foreground",
      },
      size: {
        default: "min-h-9 px-3.5 sm:min-h-8",
        sm: "min-h-9 rounded-md px-2.5 text-xs sm:min-h-7",
        lg: "min-h-10 rounded-md px-6 text-sm",
        icon: "size-9 sm:size-8",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean
  }) {
  const Comp = asChild ? Slot : "button"

  return (
    <Comp
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button }
