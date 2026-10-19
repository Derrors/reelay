interface PageHeaderProps {
  /** 保留参数以兼容旧页面；新版页头不再展示眉标。 */
  eyebrow?: string;
  title: string;
  description: string;
  action?: React.ReactNode;
}

/** 旧页面使用的页头，样式与 app/Page 保持一致；新页面请直接用 Page。 */
export default function PageHeader({ title, description, action }: PageHeaderProps) {
  return (
    <header className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-[22px] font-semibold tracking-tight text-foreground sm:text-2xl">{title}</h1>
        <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">{description}</p>
      </div>
      {action ? <div className="flex shrink-0 flex-wrap items-center gap-2">{action}</div> : null}
    </header>
  );
}
