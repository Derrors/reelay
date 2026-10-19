import { Link } from 'react-router-dom';
import { Compass } from 'lucide-react';

export default function NotFoundPage() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center p-6 text-center">
      <div className="grid size-14 place-items-center rounded-2xl bg-primary/10 text-primary">
        <Compass className="size-7" />
      </div>
      <h1 className="mt-5 text-5xl font-bold tracking-tight text-primary">404</h1>
      <p className="mt-3 text-[15px] font-medium">页面走丢了</p>
      <p className="mt-1 text-sm text-muted-foreground">你要找的页面不存在或已被移动</p>
      <Link to="/dashboard" className="mt-6 rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground hover:opacity-90">
        回到运行总览
      </Link>
    </div>
  );
}
