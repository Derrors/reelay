"use client"

import * as React from "react"
import * as SwitchPrimitive from "@radix-ui/react-switch"

import { cn } from "@/lib/utils"

function Switch({
  className,
  ...props
}: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        "peer data-[state=checked]:bg-primary data-[state=unchecked]:bg-hover-2 focus-visible:ring-ring/25 relative inline-flex h-[18px] w-[30px] shrink-0 items-center rounded-full p-px after:absolute after:-inset-x-2 after:-inset-y-3 after:content-[''] shadow-[inset_0_1px_2px_oklch(0%_0_0/0.1)] transition-colors duration-200 outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
        className={cn(
          "pointer-events-none block size-4 rounded-full bg-white shadow-[0_1px_3px_oklch(0%_0_0/0.25)] ring-0 transition-transform duration-200 ease-(--ease-out-strong) data-[state=checked]:translate-x-3 data-[state=unchecked]:translate-x-0"
        )}
      />
    </SwitchPrimitive.Root>
  )
}

export { Switch }
