"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useLang } from "@/components/Lang";

export default function Nav() {
  const pathname = usePathname();
  const { lang, setLang, t } = useLang();
  const [llmReady, setLlmReady] = useState<boolean | null>(null);

  useEffect(() => {
    api.health()
      .then(h => setLlmReady(h.llm_ready))
      .catch(() => setLlmReady(null));
  }, [pathname]);

  const tabs = [
    { href: "/", label: t("nav_chat"), match: (p: string) => p === "/" },
    { href: "/library", label: t("nav_library"), match: (p: string) => p.startsWith("/library") },
    { href: "/settings", label: t("nav_settings"), match: (p: string) => p.startsWith("/settings") },
  ];

  return (
    <header className="topnav">
      <div className="logo"><span className="brain">🧠</span>Cortex</div>
      <nav>
        {tabs.map(x => (
          <Link key={x.href} href={x.href} className={x.match(pathname) ? "active" : ""}>
            {x.label}
          </Link>
        ))}
      </nav>
      <div className="spacer" />
      <button
        className="lang-toggle"
        onClick={() => setLang(lang === "zh" ? "en" : "zh")}
        title="切换语言 / Switch language"
      >
        {lang === "zh" ? "中 / EN" : "中 / EN"}
      </button>
      <div className="status">
        <span className={`dot ${llmReady ? "ok" : "bad"}`} />
        {llmReady === null
          ? t("nav_backend_off")
          : llmReady ? t("nav_llm_ok") : t("nav_llm_no")}
      </div>
    </header>
  );
}
