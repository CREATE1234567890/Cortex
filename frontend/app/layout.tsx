import type { Metadata } from "next";
import Nav from "@/components/Nav";
import { LangProvider } from "@/components/Lang";
import "./globals.css";

export const metadata: Metadata = {
  title: "Cortex — 个人科研知识库问答",
  description: "页码级可溯源引用的个人文献库问答 Agent",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <LangProvider>
          <Nav />
          {children}
        </LangProvider>
      </body>
    </html>
  );
}
