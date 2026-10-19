import { ArrowLeft, Compass, LayoutDashboard } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';

export default function NotFoundPage() {
  const navigate = useNavigate();
  return (
    <div className="flex min-h-svh flex-col items-center justify-center px-5 py-10 text-center">
      <span className="grid size-12 place-items-center rounded-full bg-primary/10 text-primary">
        <Compass className="size-6" />
      </span>
      <p className="mt-5 text-sm font-semibold tabular-nums text-primary">404</p>
      <h1 className="mt-1 text-[22px] font-semibold tracking-tight">找不到这个页面</h1>
      <p className="mt-1.5 max-w-sm text-sm leading-6 text-muted-foreground">链接可能输错了，或者页面已经被移动。</p>
      <div className="mt-6 flex flex-wrap justify-center gap-2">
        <Button asChild>
          <Link to="/dashboard"><LayoutDashboard />回到总览</Link>
        </Button>
        {window.history.length > 1 ? (
          <Button variant="outline" className="bg-card" onClick={() => navigate(-1)}><ArrowLeft />返回上一页</Button>
        ) : null}
      </div>
    </div>
  );
}
