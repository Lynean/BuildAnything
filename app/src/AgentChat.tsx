import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { ScreenChip, addScreenLinks } from "./Linked";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export interface ChatMessage { from: "you" | "agent" | "other"; text: string; label?: string; details?: string; }

export function Rendered({ text }: { text: string }) {
  // Department and screen names become shortcuts to that screen.
  return <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={(url) => url} components={{ a: ({ href, children, ...props }) => href?.startsWith("#screen:") ? <ScreenChip screen={decodeURIComponent(href.slice(8))}>{children}</ScreenChip> : <a {...props} href={href} target="_blank" rel="noreferrer">{children}</a> }}>{addScreenLinks(text)}</ReactMarkdown></div>;
}

/** The box you type to an agent in. Enter sends, and Shift+Enter starts a new line. */
export interface ChatPhoto { name: string; data: string; }

export function AgentComposer({ agent, disabled, onSend, placeholder, compact, allowPhotos }: { agent: string; disabled?: boolean; onSend: (text: string, photos?: ChatPhoto[]) => void; placeholder?: string; compact?: boolean; allowPhotos?: boolean }) {
  const [text, setText] = useState("");
  const [photos, setPhotos] = useState<ChatPhoto[]>([]);
  const send = () => {
    const value = text.trim();
    if ((!value && !photos.length) || disabled) return;
    onSend(value, photos);
    setText("");
    setPhotos([]);
  };
  const add = (files: FileList | null) => Array.from(files ?? []).slice(0, 4).forEach((file) => {
    const reader = new FileReader();
    reader.onload = () => setPhotos((p) => [...p, { name: file.name, data: String(reader.result) }]);
    reader.readAsDataURL(file);
  });
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };
  return (
    <div className={`agent-composer ${compact ? "compact" : ""}`}>
      {allowPhotos && <label className="agent-attach" title="Attach a photo">+<input type="file" accept="image/png,image/jpeg,image/webp" multiple aria-label="Attach a photo" onChange={(e) => { add(e.target.files); e.target.value = ""; }} /></label>}
      {photos.length > 0 && <span className="agent-photos">{photos.map((p) => p.name).join(", ")}</span>}
      <textarea rows={compact ? 2 : 1} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} placeholder={placeholder ?? `Message ${agent}`} aria-label={`Message ${agent}`} />
      <button className="agent-send" disabled={disabled || (!text.trim() && !photos.length)} onClick={send} aria-label="Send">↑</button>
    </div>
  );
}

export default function AgentChat({ agent, role, messages, earlier, between, canQueue, allowPhotos, thinking, onSend, placeholder, compact, empty, children }: { agent: string; role: string; messages: ChatMessage[]; earlier?: ChatMessage[]; between?: ChatMessage[]; canQueue?: boolean; allowPhotos?: boolean; thinking?: string; onSend: (text: string, photos?: ChatPhoto[]) => void; placeholder?: string; compact?: boolean; empty?: string; children?: ReactNode }) {
  const log = useRef<HTMLDivElement>(null);
  useEffect(() => { if (log.current) log.current.scrollTop = log.current.scrollHeight; }, [messages.length, thinking]);
  return (
    <section className={`agent-chat ${compact ? "compact" : ""}`} aria-label={`Conversation with ${agent}`}>
      <header className="agent-head">
        <span className="agent-avatar" aria-hidden>{agent[0]}</span>
        <div><b>{agent}</b><small>{role}</small></div>
      </header>
      <div className="agent-log" ref={log}>
        {earlier && earlier.length > 0 && <details className="agent-earlier"><summary>Earlier questions from the concept stage ({earlier.length})</summary>{earlier.map((m, i) => <div className={`bubble ${m.from}`} key={i}>{m.label && <div className="bubble-label">{m.label}</div>}<Rendered text={m.text} /></div>)}</details>}
        {between && between.length > 0 && <details className="agent-earlier"><summary>Messages between departments ({between.length})</summary>{between.map((m, i) => <div className={`bubble ${m.from}`} key={i}>{m.label && <div className="bubble-label">{m.label}</div>}<Rendered text={m.text} /></div>)}</details>}
        {messages.map((m, i) => m.from === "you"
          ? <div className="bubble you" key={i}>{m.text}</div>
          : <div className={`bubble ${m.from}`} key={i}>{m.label && <div className="bubble-label">{m.label}</div>}<Rendered text={m.text} />{m.details && <details className="agent-earlier"><summary>Show more</summary><Rendered text={m.details} /></details>}</div>)}
        {messages.length === 0 && !thinking && <p className="hint">{empty ?? `${agent} will start here as soon as Headquarters has briefed it.`}</p>}
        {thinking && <div className="agent-thinking" role="status"><i /><i /><i /><span>{thinking}</span></div>}
      </div>
      {children}
      <AgentComposer agent={agent} disabled={!canQueue && !!thinking} onSend={onSend} placeholder={placeholder} compact={compact} allowPhotos={allowPhotos} />
    </section>
  );
}
