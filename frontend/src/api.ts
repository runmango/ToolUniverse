export type ToolEvent = { id: string; name: string; arguments: Record<string, unknown>; status: string; success?: boolean; output?: unknown };
export type Message = { id: string; role: 'user' | 'assistant'; content: string; tools: ToolEvent[]; status: string; created_at: number };
export type Session = { id: string; title: string; thread_id?: string; model?: string; created_at: number; updated_at: number; messages?: Message[]; active?: boolean };
export type Connection = { installed: boolean; connected: boolean; account?: { type: string; email?: string; planType?: string }; error?: string; login_pending?: boolean; login_result?: { success: boolean; error?: string } };
export type Status = { tools: { connected: boolean; loaded_tools_count: number }; connection: Connection; version: string };
export type Model = { id: string; model: string; displayName: string; isDefault?: boolean };
export type ScientificTool = { name: string; description: string; category?: string; type?: string };
export type ToolInfo = ScientificTool & { parameter?: { properties?: Record<string, { type?: string | string[]; description?: string; default?: unknown; enum?: unknown[]; required?: boolean }>; required?: string[] }; mcp_annotations?: { readOnlyHint?: boolean; destructiveHint?: boolean } };

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, { ...options, headers: { 'Content-Type': 'application/json', 'X-Workbench-Client': '1', ...options.headers } });
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`;
    try { const error = await response.json(); detail = typeof error.detail === 'string' ? error.detail : detail; } catch { /* non-JSON upstream error */ }
    throw new Error(detail);
  }
  return response.json();
}

export const post = <T,>(path: string, body: unknown = {}) => api<T>(path, { method: 'POST', body: JSON.stringify(body) });

export async function streamChat(body: unknown, signal: AbortSignal, onEvent: (event: Record<string, any>) => void) {
  const response = await fetch('/api/chat', { method: 'POST', signal, headers: { 'Content-Type': 'application/json', 'X-Workbench-Client': '1' }, body: JSON.stringify(body) });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `连接失败 (${response.status})`);
  }
  if (!response.body) throw new Error('浏览器不支持流式响应');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let position: number;
      while ((position = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, position); buffer = buffer.slice(position + 2);
        for (const line of block.split('\n')) {
          if (line.startsWith('data: ')) onEvent(JSON.parse(line.slice(6)));
        }
      }
    }
  } finally { reader.releaseLock(); }
}

export function download(name: string, text: string, type = 'text/plain;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
