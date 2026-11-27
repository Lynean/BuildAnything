import { useEffect, useRef, useState } from "react";
import AgentChat, { type ChatMessage, type ChatPhoto } from "./AgentChat";
import { call } from "./api";

interface Message { role: string; text: string; from: string | null; early?: boolean; details?: string; internal?: boolean; choices?: string[]; questions?: { question: string; options: string[] }[]; }
interface Thread { messages: Message[]; running: string | null; eventCount: number; }

const ROLE: Record<string, string> = { Electrical: "Your one contact for Electrical", Mechanical: "Your one contact for Mechanical", Software: "Your one contact for Software", Procurement: "Your one contact for Procurement" };

/** One agent per department. It speaks first once Headquarters has briefed it. */
export default function DepartmentChat({ projectId, department, compact = true, onChange }: { projectId: string; department: string; compact?: boolean; onChange?: () => void }) {
  const [thread, setThread] = useState<Thread | null>(null);
  const [sending, setSending] = useState(0);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<string[]>([]);
  const count = useRef(0);
  const path = `/projects/${projectId}/departments/${department}`;

  useEffect(() => {
    let alive = true;
    count.current = 0;
    const load = () => call<Thread>(path).then((t) => {
      if (!alive) return;
      setThread(t);
      if (count.current && t.eventCount !== count.current) onChange?.();
      count.current = t.eventCount;
    }).catch(() => undefined);
    load();
    const timer = window.setInterval(load, 3000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [path]);

  const send = async (text: string, photos: ChatPhoto[] = []) => {
    const shown = text || "Photo";
    setSending((n) => n + 1); setError(""); setPending((p) => [...p, shown]);
    try { setThread(await call<Thread>(path, "POST", { message: text, photos })); onChange?.(); } catch (e) { setError((e as Error).message); } finally { setSending((n) => n - 1); setPending((p) => p.filter((x) => x !== shown)); }
  };

  const toChat = (m: Message): ChatMessage => m.role === "you" ? { from: "you", text: m.text } : m.role === "request" ? { from: "other", text: m.text, label: m.from === "Headquarters" ? "From Headquarters" : m.from === "You" ? "Your answer, passed on by Headquarters" : `Question from ${m.from}` } : { from: "agent", text: m.text, details: m.details, choices: m.choices, questions: m.questions, label: m.internal ? `${department} answering another department` : undefined };
  const earlier = (thread?.messages ?? []).filter((m) => m.early).map(toChat);
  const messages = (thread?.messages ?? []).filter((m) => !m.early && !m.internal).map(toChat);
  const between = (thread?.messages ?? []).filter((m) => !m.early && m.internal).map(toChat);
  pending.forEach((text) => messages.push({ from: "you", text }));
  return (
    <AgentChat agent={`${department} Lead`} role={ROLE[department]} messages={messages} earlier={earlier} between={between} canQueue allowPhotos thinking={sending ? "Working on it" : thread?.running ?? undefined} onSend={send} compact={compact}>
      {error && <p className="error" role="alert">{error}</p>}
    </AgentChat>
  );
}
