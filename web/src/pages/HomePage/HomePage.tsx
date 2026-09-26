export default function HomePage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3 p-8 text-center">
      <h1 className="text-2xl font-semibold">应用已就绪</h1>
      <p className="text-muted-foreground">
        编辑 <code className="rounded bg-muted px-1.5 py-0.5 text-sm">src/app.tsx</code> 开始开发
      </p>
    </main>
  );
}
