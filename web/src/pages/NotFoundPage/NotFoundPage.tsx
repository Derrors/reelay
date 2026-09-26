import { Link } from "react-router-dom";

export default function NotFoundPage() {
  return (
    <div className="flex flex-col items-center justify-center py-24">
      <h1 className="text-6xl font-bold mb-4">404</h1>
      <p className="text-lg text-muted-foreground mb-8">页面不存在</p>
      <Link to="/" className="text-primary hover:underline">返回首页</Link>
    </div>
  );
}
