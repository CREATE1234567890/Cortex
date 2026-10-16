# -*- coding: utf-8 -*-
"""CLI：把知识库文件夹全量导入（增量，跳过已 indexed）。

用法（后端服务须已在 8300 运行，保证索引在服务进程内一致）：
  python scripts/ingest_all.py [可选根目录]
"""
import os
import sys
import time

import httpx

API = "http://127.0.0.1:8300"


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else None
    with httpx.Client(timeout=300) as c:
        r = c.post(f"{API}/api/library/scan", json={"root": root or ""})
        r.raise_for_status()
        data = r.json()
        files = [f["path"] for f in data["files"]]
        print(f"扫描到 {len(files)} 个 PDF（根目录：{data['root']}）")

        r = c.post(f"{API}/api/library/ingest", json={"paths": files, "incremental": True})
        r.raise_for_status()
        res = r.json()
        if not res.get("started"):
            print("未能开始：", res.get("reason"))
            return
        print(f"已入队 {res.get('queued')} 个，开始后台摄取…")

        while True:
            try:
                job = c.get(f"{API}/api/library/job", timeout=120).json()
            except httpx.HTTPError as e:
                print(f"\n(轮询暂不可达：{type(e).__name__}，10s 后重试)")
                time.sleep(10)
                continue
            print(f"\r[{job['done']+job['failed']}/{job['total']}] "
                  f"成功 {job['done']} 失败 {job['failed']} 当前: {job['current_file'] or '…'}    ",
                  end="", flush=True)
            if job["state"] in ("done", "error"):
                print(f"\n完成：{job['state']} {job['message'] or ''}")
                break
            time.sleep(5)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    main()
