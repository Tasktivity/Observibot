import { useState } from 'react';
import { api, type ChatResponse } from '../api/client';
import { ChatVisualization } from '../components/ChatVisualization';
import { WIDGET_REGISTRY } from '../widgets/WidgetRegistry';

interface Message {
  role: 'user' | 'assistant';
  content: string;
  response?: ChatResponse;
}

interface ChatProps {
  onPin?: (data: { widget_type: string; title: string; config?: unknown; data_source?: unknown }) => void;
}

export function Chat({ onPin }: ChatProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || loading) return;

    const question = input.trim();
    setInput('');
    setMessages((prev) => [...prev, { role: 'user', content: question }]);
    setLoading(true);

    try {
      const response = await api.chat.query(question);
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: response.answer, response },
      ]);
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: 'Sorry, something went wrong.' },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handlePin = (resp: ChatResponse) => {
    if (!onPin || !resp.widget_plan) return;
    const plan = resp.widget_plan as Record<string, unknown>;
    onPin({
      widget_type: (plan.widget_type as string) ?? 'table',
      title: (plan.title as string) ?? 'Chat result',
      config: plan.config ?? plan.encoding ?? {},
      data_source: plan.data,
    });
  };

  return (
    <div className="flex flex-col h-full">
      <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
        Chat
      </h2>
      <div className="flex-1 overflow-y-auto space-y-3 mb-3">
        {messages.length === 0 && (
          <p className="text-slate-500 text-sm">
            Ask questions about your infrastructure. Try: &quot;Show recent metrics&quot;
          </p>
        )}
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`p-3 rounded-lg text-sm ${
              msg.role === 'user'
                ? 'bg-sky-500/20 text-sky-100 ml-8'
                : 'bg-slate-800 text-slate-200 mr-8'
            }`}
          >
            {msg.content}

            {msg.response?.vega_lite_spec && (
              <ChatVisualization spec={msg.response.vega_lite_spec} />
            )}

            {msg.response?.widget_plan && !msg.response.vega_lite_spec && (() => {
              const plan = msg.response!.widget_plan as Record<string, unknown>;
              const wtype = plan.widget_type as string;
              const Comp = WIDGET_REGISTRY[wtype];
              if (!Comp) return null;
              return (
                <div className="mt-2 h-32">
                  <Comp
                    config={(plan.config as Record<string, unknown>) ?? null}
                    data={(plan.data as unknown[]) ?? []}
                    title={(plan.title as string) ?? ''}
                  />
                </div>
              );
            })()}

            {msg.response?.sql_query && (
              <details className="mt-2">
                <summary className="text-xs text-slate-400 cursor-pointer">Show SQL</summary>
                <pre className="mt-1 text-xs bg-slate-900 p-2 rounded overflow-x-auto">
                  {msg.response.sql_query}
                </pre>
              </details>
            )}

            {msg.response?.widget_plan && onPin && (
              <button
                onClick={() => handlePin(msg.response!)}
                className="mt-2 text-xs text-sky-400 hover:text-sky-300"
              >
                Pin to Dashboard
              </button>
            )}
          </div>
        ))}
        {loading && (
          <div className="bg-slate-800 text-slate-400 p-3 rounded-lg text-sm mr-8 animate-pulse">
            Thinking...
          </div>
        )}
      </div>
      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about your system..."
          className="flex-1 px-4 py-2 rounded bg-slate-800 text-white border border-slate-700 focus:outline-none focus:border-sky-400 text-sm"
        />
        <button
          type="submit"
          disabled={loading}
          className="px-4 py-2 bg-sky-500 hover:bg-sky-600 disabled:bg-slate-600 text-white rounded text-sm font-medium transition"
        >
          Send
        </button>
      </form>
    </div>
  );
}
