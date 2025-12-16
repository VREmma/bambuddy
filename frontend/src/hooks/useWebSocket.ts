import { useEffect, useRef, useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

interface WebSocketMessage {
  type: string;
  printer_id?: number;
  data?: Record<string, unknown>;
}

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const queryClient = useQueryClient();
  const [isConnected, setIsConnected] = useState(false);

  // Debounce invalidations to prevent rapid re-render cascades
  const pendingInvalidations = useRef<Set<string>>(new Set());
  const invalidationTimeoutRef = useRef<number | null>(null);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/v1/ws`;

    const ws = new WebSocket(wsUrl);

    let pingInterval: number | null = null;

    ws.onopen = () => {
      console.log('[WebSocket] Connected');
      setIsConnected(true);
      // Start ping interval
      pingInterval = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, 30000);
    };

    ws.onmessage = (event) => {
      try {
        const message: WebSocketMessage = JSON.parse(event.data);
        handleMessage(message);
      } catch {
        // Ignore parse errors
      }
    };

    ws.onclose = (event) => {
      console.log('[WebSocket] Closed', event.code, event.reason);
      if (pingInterval) {
        clearInterval(pingInterval);
        pingInterval = null;
      }
      setIsConnected(false);
      wsRef.current = null;

      // Reconnect after 3 seconds
      reconnectTimeoutRef.current = window.setTimeout(() => {
        connect();
      }, 3000);
    };

    ws.onerror = (error) => {
      console.error('[WebSocket] Error', error);
      ws.close();
    };

    wsRef.current = ws;
  }, []);

  // Debounced invalidation helper - coalesces multiple rapid invalidations
  const debouncedInvalidate = useCallback((queryKey: string) => {
    pendingInvalidations.current.add(queryKey);

    // Clear existing timeout
    if (invalidationTimeoutRef.current) {
      clearTimeout(invalidationTimeoutRef.current);
    }

    // Schedule invalidation after a short delay (100ms)
    invalidationTimeoutRef.current = window.setTimeout(() => {
      const keys = Array.from(pendingInvalidations.current);
      pendingInvalidations.current.clear();
      invalidationTimeoutRef.current = null;

      // Use requestAnimationFrame to avoid blocking the main thread
      requestAnimationFrame(() => {
        keys.forEach((key) => {
          queryClient.invalidateQueries({ queryKey: [key] });
        });
      });
    }, 100);
  }, [queryClient]);

  const handleMessage = useCallback((message: WebSocketMessage) => {
    switch (message.type) {
      case 'printer_status':
        // Update the printer status in the query cache
        if (message.printer_id !== undefined) {
          queryClient.setQueryData(
            ['printerStatus', message.printer_id],
            (old: Record<string, unknown> | undefined) => {
              const merged = {
                ...old,
                ...message.data,
              };
              // Preserve last known wifi_signal if new value is null
              if (merged.wifi_signal == null && old?.wifi_signal != null) {
                merged.wifi_signal = old.wifi_signal;
              }
              return merged;
            }
          );
        }
        break;

      case 'print_complete':
        // Invalidate archives to refresh the list (debounced)
        debouncedInvalidate('archives');
        debouncedInvalidate('archiveStats');
        break;

      case 'archive_created':
        // Invalidate archives to show new archive (debounced)
        debouncedInvalidate('archives');
        debouncedInvalidate('archiveStats');
        break;

      case 'archive_updated':
        // Invalidate archives to refresh (debounced)
        debouncedInvalidate('archives');
        break;

      case 'pong':
        // Keepalive response, ignore
        break;
    }
  }, [queryClient, debouncedInvalidate]);

  useEffect(() => {
    connect();

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (invalidationTimeoutRef.current) {
        clearTimeout(invalidationTimeoutRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [connect]);

  const sendMessage = useCallback((message: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(message));
    }
  }, []);

  return { isConnected, sendMessage };
}
