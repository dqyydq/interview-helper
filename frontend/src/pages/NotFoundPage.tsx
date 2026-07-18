import { ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <section className="not-found" aria-labelledby="not-found-title">
      <p className="eyebrow">ERROR / 404</p>
      <h1 id="not-found-title">页面不存在</h1>
      <p>这个地址不属于当前工作台。</p>
      <Link to="/interviews">
        <ArrowLeft size={16} aria-hidden="true" />
        返回模拟面试
      </Link>
    </section>
  );
}
