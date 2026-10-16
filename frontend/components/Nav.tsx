"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function Nav() {
  const pathname = usePathname();
  const [llmReady, setLlmReady] = useState<boolean | null>(null);

  useEffect(() => {
    api.health()
      .then(h => setLlmReady(h.llm_ready))
      .catch(() => setLlmReady(null));
  }, [pathname]);

  const tabs = [
    { href: "/", label: "对话", match: (p: string) => p === "/" },
    { href: "/library", label: "文献库", match: (p: string) => p.startsWith("/library") },
    { href: "/settings", label: "设置", match: (p: string) => p.startsWith("/settings") },
  ];

  return (
    <header className="topnav">
      <div className="logo"><span className="brain">🧠</span>Cortex</div>
      <nav>
        {tabs.map(t => (
          <Link key={t.href} href={t.href} className={t.match(pathname) ? "active" : ""}>
            {t.label}
          </Link>
        ))}
      </nav>
      <div className="spacer" />
      <div className="status">
        <span className={`dot ${llmReady ? "ok" : "bad"}`} />
        {llmReady === null ? "后端未连接" : llmReady ? "LLM 已配置" : "未配置 LLM（证据模式）"}
      </div>
    </header>
  );
}
