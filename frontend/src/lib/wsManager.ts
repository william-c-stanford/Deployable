/**
 * WebSocket Connection Manager
 *
 * Centralized manager for all WebSocket connections. Supports:
 * - Token-based authentication via query parameter
 * - Teardown of all connections on role switch
 * - Re-establishment of connections with a new token
 * - Topic-based subscriptions with auto-reconnect
 * - Exponential backoff with jitter on reconnection
 * - Connection state change notifications (no polling needed)
 * - Multi-topic subscription from a single hook
 */

import type { WSConnectionStatus } from "@/types";

type MessageHandler = (data: any) => void;
type StatusChangeHandler = (id: string, status: WSConnectionStatus) => void;

interface WSConnection {
  id: string;
  topic: string;
  ws: WebSocket | null;
  onMessage: MessageHandler;
  reconnectTimer: ReturnType<typeof setTimeout> | null;
  heartbeatTimer: ReturnType<typeof setInterval> | null;
  active: boolean;
  status: WSConnectionStatus;
  reconnectAttempt: number;
}

/** Exponential backoff config */
const BACKOFF_BASE_MS = 1000;
const BACKOFF_MAX_MS = 30000;
const BACKOFF_MULTIPLIER = 2;
const HEARTBEAT_INTERVAL_MS = 30000;

/** Calculate backoff delay with jitter */
function getBackoffDelay(attempt: number): number {
  const delay = Math.min(
    BACKOFF_BASE_MS * Math.pow(BACKOFF_MULTIPLIER, attempt),
    BACKOFF_MAX_MS,
  );
  // Add jitter: ±25%
  const jitter = delay * 0.25 * (Math.random() * 2 - 1);
  return Math.max(500, delay + jitter);
}

class WebSocketManager {
  private connections: Map<string, WSConnection> = new Map();
  private token: string | null = null;
  private baseUrl: string = "";
  private listeners: Set<() => void> = new Set();
  private statusListeners: Set<StatusChangeHandler> = new Set();

  constructor() {
    this.computeBaseUrl();

    // Listen for browser online/offline events for smart reconnection
    if (typeof window !== "undefined") {
      window.addEventListener("online", () => this.handleOnline());
      window.addEventListener("offline", () => this.handleOffline());

      // Reconnect on visibility change (tab comes back to focus)
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") {
          this.reconnectDisconnected();
        }
      });
    }
  }

  private computeBaseUrl() {
    if (typeof window !== "undefined") {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const host = import.meta.env.VITE_WS_HOST || window.location.host;
      this.baseUrl = `${protocol}//${host}`;
    }
  }

  /** Set the current auth token. Does NOT auto-reconnect; call reconnectAll() after. */
  setToken(token: string | null) {
    this.token = token;
  }

  getToken(): string | null {
    return this.token;
  }

  /** Register a topic subscription. Returns an unregister function. */
  register(
    id: string,
    topic: string,
    onMessage: MessageHandler,
  ): () => void {
    // If already registered with same id, tear it down first
    if (this.connections.has(id)) {
      this.teardownConnection(id);
    }

    const conn: WSConnection = {
      id,
      topic,
      ws: null,
      onMessage,
      reconnectTimer: null,
      heartbeatTimer: null,
      active: true,
      status: "connecting",
      reconnectAttempt: 0,
    };

    this.connections.set(id, conn);
    this.connectOne(conn);
    this.notifyListeners();

    return () => {
      this.teardownConnection(id);
      this.connections.delete(id);
      this.notifyListeners();
    };
  }

  /** Connect a single connection entry */
  private connectOne(conn: WSConnection) {
    if (!conn.active) return;

    this.updateStatus(conn, conn.reconnectAttempt > 0 ? "reconnecting" : "connecting");

    const tokenParam = this.token ? `?token=${encodeURIComponent(this.token)}` : "";
    const url = `${this.baseUrl}/ws/${conn.topic}${tokenParam}`;

    try {
      const ws = new WebSocket(url);
      conn.ws = ws;

      ws.onopen = () => {
        conn.reconnectAttempt = 0; // Reset backoff on successful connection
        this.updateStatus(conn, "connected");

        // Start heartbeat every 30s
        conn.heartbeatTimer = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send("ping");
          }
        }, HEARTBEAT_INTERVAL_MS);

        this.notifyListeners();
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === "pong" || data.event_type === "pong") return;
          conn.onMessage(data);
        } catch {
          // Non-JSON message, ignore
        }
      };

      ws.onclose = (event) => {
        this.cleanupTimers(conn);

        // Only reconnect if still active and not a clean close (code 1000)
        if (conn.active) {
          this.updateStatus(conn, "reconnecting");
          const delay = getBackoffDelay(conn.reconnectAttempt);
          conn.reconnectAttempt++;
          conn.reconnectTimer = setTimeout(() => this.connectOne(conn), delay);
        } else {
          this.updateStatus(conn, "disconnected");
        }

        this.notifyListeners();
      };

      ws.onerror = () => {
        // onerror is always followed by onclose, so just close
        ws.close();
      };
    } catch {
      // Connection failed, retry with backoff
      if (conn.active) {
        this.updateStatus(conn, "reconnecting");
        const delay = getBackoffDelay(conn.reconnectAttempt);
        conn.reconnectAttempt++;
        conn.reconnectTimer = setTimeout(() => this.connectOne(conn), delay);
      }
    }
  }

  private cleanupTimers(conn: WSConnection) {
    if (conn.reconnectTimer) {
      clearTimeout(conn.reconnectTimer);
      conn.reconnectTimer = null;
    }
    if (conn.heartbeatTimer) {
      clearInterval(conn.heartbeatTimer);
      conn.heartbeatTimer = null;
    }
  }

  private updateStatus(conn: WSConnection, status: WSConnectionStatus) {
    const prevStatus = conn.status;
    conn.status = status;
    if (prevStatus !== status) {
      for (const listener of this.statusListeners) {
        try {
          listener(conn.id, status);
        } catch {
          // Don't let one failing listener block others
        }
      }
    }
  }

  /** Tear down a single connection */
  private teardownConnection(id: string) {
    const conn = this.connections.get(id);
    if (!conn) return;

    conn.active = false;
    this.cleanupTimers(conn);

    if (conn.ws) {
      try {
        conn.ws.close(1000, "teardown");
      } catch {
        // ignore
      }
      conn.ws = null;
    }

    this.updateStatus(conn, "disconnected");
  }

  /** Tear down ALL active connections. Called on role switch. */
  teardownAll() {
    for (const [id] of this.connections) {
      this.teardownConnection(id);
    }
    this.notifyListeners();
  }

  /** Reconnect all registered connections (with current token). Called after role switch. */
  reconnectAll() {
    for (const [, conn] of this.connections) {
      conn.active = true;
      conn.reconnectAttempt = 0;
      this.connectOne(conn);
    }
    this.notifyListeners();
  }

  /** Reconnect only disconnected/failed connections */
  private reconnectDisconnected() {
    for (const [, conn] of this.connections) {
      if (conn.active && conn.status !== "connected") {
        conn.reconnectAttempt = 0; // Reset backoff
        this.cleanupTimers(conn); // Cancel any pending reconnect
        this.connectOne(conn);
      }
    }
  }

  /** Handle browser going online */
  private handleOnline() {
    this.reconnectDisconnected();
  }

  /** Handle browser going offline */
  private handleOffline() {
    for (const [, conn] of this.connections) {
      if (conn.active) {
        this.updateStatus(conn, "disconnected");
      }
    }
    this.notifyListeners();
  }

  /** Get connected status for a specific connection id */
  isConnected(id: string): boolean {
    const conn = this.connections.get(id);
    return conn?.ws?.readyState === WebSocket.OPEN || false;
  }

  /** Get the status of a specific connection */
  getStatus(id: string): WSConnectionStatus {
    const conn = this.connections.get(id);
    return conn?.status ?? "disconnected";
  }

  /** Get total active connection count */
  get activeCount(): number {
    let count = 0;
    for (const [, conn] of this.connections) {
      if (conn.ws?.readyState === WebSocket.OPEN) count++;
    }
    return count;
  }

  /** Get all subscription statuses */
  getSubscriptions(): Array<{ id: string; topic: string; status: WSConnectionStatus }> {
    const result: Array<{ id: string; topic: string; status: WSConnectionStatus }> = [];
    for (const [, conn] of this.connections) {
      result.push({ id: conn.id, topic: conn.topic, status: conn.status });
    }
    return result;
  }

  /** Subscribe to generic connection state changes */
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Subscribe to status changes for specific connections */
  onStatusChange(listener: StatusChangeHandler): () => void {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

  private notifyListeners() {
    for (const listener of this.listeners) {
      listener();
    }
  }
}

/** Singleton WebSocket manager instance */
export const wsManager = new WebSocketManager();
