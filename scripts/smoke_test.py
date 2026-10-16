# -*- coding: utf-8 -*-
"""E2E 冒烟测试：摄取论文子集 + 检索验证。"""
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")
os.environ.setdefault("PYTHONUTF8", "1")
sys.path.insert(0, r"D:\WorkSpace\Agent\Cortex\backend")

import fitz

fitz.TOOLS.mupdf_display_errors(False)

from app.library import start_ingest, get_job, list_documents  # noqa: E402

ROOT = r"C:\Users\yang'jia'cai\Desktop\论文"
PICKS = [
    "Swin-Unet.pdf",
    "CSWin-UNet.pdf",
    "U2-Net.pdf",
    "ASM-UNet.pdf",
    "MSLAU-Net.pdf",
    "I_2U-Net A dual-path U-Net with rich information interaction for medical image segmentation.pdf",
    "U-Net模型改进及其在医学图像分割上的研究综述_激光与光电子学进展.pdf",
    "ImageNet_Classification_with_Deep_Convolutional_Neural_Networks_AlexNet_.pdf",
    "NeurIPS-2024-vmamba-visual-state-space-model-Paper-Conference.pdf",
]
paths = []
for p in PICKS:
    full = os.path.join(ROOT, p)
    print(("OK " if os.path.exists(full) else "MISS ") + p)
    if os.path.exists(full):
        paths.append(full)

print(f"ingesting {len(paths)} docs")
t0 = time.time()
res = start_ingest(paths, incremental=False)
print("started:", res)
while True:
    job = get_job()
    if job["state"] in ("done", "error"):
        break
    time.sleep(2)
print(f"job: {job['state']} done {job['done']} failed {job['failed']} in {time.time()-t0:.0f}s")
for d in list_documents():
    print(" ", d["status"], d["n_chunks"], "chunks |", (d["title"] or "")[:60], "| err:", (d["error"] or "")[:80])

# --- 检索验证 ---
from app.retriever import retrieve  # noqa: E402

queries = [
    "Swin Transformer 如何用于医学图像分割？",
    "which model is better for salient object detection U2-Net",
    "AlexNet 在 ImageNet 上的 top-5 错误率是多少？",
    "VMamba 的视觉状态空间模块结构是怎样的",
]
for q in queries:
    evs = retrieve(q, top_k=5)
    print(f"\nQ: {q}")
    for ev in evs[:3]:
        print(f"  [{ev.n}] {ev.title[:50]} p.{ev.page_start} score={ev.score:.4f} | {ev.snippet[:80]}")
