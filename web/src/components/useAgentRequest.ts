import { useCallback, useEffect, useRef, useState } from "react";
import { AGENT_TIMEOUT_MS } from "../agent";

/** One cancellable parse at a time. Stale/unmounted requests cannot replace a preview. */
export function useAgentRequest() {
  const current = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      current.current?.abort();
      current.current = null;
    };
  }, []);

  const cancel = useCallback(() => {
    const request = current.current;
    current.current = null;
    request?.abort();
    setBusy(false);
  }, []);

  const run = useCallback(async <T,>(request: (signal: AbortSignal) => Promise<T>, onSuccess: (value: T) => void) => {
    if (current.current) return;
    const controller = new AbortController();
    current.current = controller;
    setBusy(true);
    setError("");
    let timedOut = false;
    const timeout = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, AGENT_TIMEOUT_MS);
    try {
      const result = await request(controller.signal);
      if (mounted.current && current.current === controller && !controller.signal.aborted) onSuccess(result);
    } catch (cause) {
      if (mounted.current && current.current === controller) {
        if (timedOut) setError("解析超过 2 分钟，请缩短文本或检查模型连接后重试");
        else if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "解析失败，请重试");
      }
    } finally {
      window.clearTimeout(timeout);
      if (mounted.current && current.current === controller) {
        current.current = null;
        setBusy(false);
      }
    }
  }, []);

  return { busy, error, setError, run, cancel };
}
