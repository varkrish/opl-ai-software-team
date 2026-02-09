import { useEffect, useRef, useCallback } from 'react';

/**
 * Custom hook for polling an async callback at a fixed interval.
 * Automatically cleans up on unmount.
 *
 * @param callback  Async function to call on each interval tick
 * @param intervalMs  Polling interval in milliseconds (default: 2000)
 * @param enabled  Whether polling is active (default: true)
 */
export function usePolling(
  callback: () => Promise<void> | void,
  intervalMs: number = 2000,
  enabled: boolean = true
): void {
  const savedCallback = useRef(callback);

  // Keep the callback ref up to date
  useEffect(() => {
    savedCallback.current = callback;
  }, [callback]);

  useEffect(() => {
    if (!enabled) return;

    // Run immediately on mount
    savedCallback.current();

    const id = setInterval(() => {
      savedCallback.current();
    }, intervalMs);

    return () => clearInterval(id);
  }, [intervalMs, enabled]);
}
