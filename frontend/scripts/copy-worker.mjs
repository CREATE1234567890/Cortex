// 把 pdf.js worker 拷进 public/，运行时零外部网络依赖
import { copyFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const src = require.resolve("pdfjs-dist/build/pdf.worker.min.mjs");
mkdirSync("public", { recursive: true });
copyFileSync(src, "public/pdf.worker.min.mjs");
console.log("pdf worker copied:", src);
