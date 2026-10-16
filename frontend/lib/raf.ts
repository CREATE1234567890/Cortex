/**
 * requestAnimationFrame 可用性探测与降级。
 *
 * 部分嵌入式浏览器（如 ZCode 内置面板）不触发 rAF 回调，
 * 而 pdf.js 的渲染循环依赖 rAF 分片执行，会导致渲染永久挂起。
 * 探测一次：若 rAF 在 400ms 内未触发，则用 setTimeout 兜底替换。
 * 探测结果进程内缓存，普通浏览器零开销。
 */

let checked = false;

export async function ensureRafWorks(): Promise<void> {
  if (checked) return;
  checked = true;
  const fired = await new Promise<boolean>(resolve => {
    let done = false;
    try {
      requestAnimationFrame(() => {
        done = true;
        resolve(true);
      });
      setTimeout(() => {
        if (!done) resolve(false);
      }, 400);
    } catch {
      resolve(false);
    }
  });
  if (!fired) {
    window.requestAnimationFrame = ((cb: FrameRequestCallback) =>
      setTimeout(() => cb(performance.now()), 16)) as unknown as typeof requestAnimationFrame;
  }
}
