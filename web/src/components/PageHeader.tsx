interface PageHeaderProps {
  eyebrow: string;
  title: string;
  description: string;
  action?: React.ReactNode;
}

export default function PageHeader({ eyebrow, title, description, action }: PageHeaderProps) {
  return (
    <header className="flex flex-col gap-4 pb-6 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <div className="mb-2 inline-flex items-center gap-1.5 rounded-full border border-primary/20 bg-primary/[0.07] px-2.5 py-0.5 text-[11px] font-medium text-primary">
          <span className="size-1 rounded-full bg-primary" />
          {eyebrow}
        </div>
        <h1 className="text-[22px] font-semibold leading-tight tracking-tight sm:text-[26px]">{title}</h1>
        <p className="mt-1.5 max-w-3xl text-[13px] leading-6 text-muted-foreground">{description}</p>
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </header>
  );
}
