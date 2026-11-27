import { useState } from "react";

export interface Question { question: string; options: string[]; }

// All of an agent's questions at once: pick an option or type your own for each, then send every answer in one message.
export default function QuestionForm({ questions, onSend }: { questions: Question[]; onSend: (text: string) => void }) {
  const [picked, setPicked] = useState<Record<number, string>>({});
  const [own, setOwn] = useState<Record<number, string>>({});
  const [typing, setTyping] = useState<Record<number, boolean>>({});
  const answer = (i: number) => (typing[i] ? (own[i] ?? "").trim() : picked[i] ?? "");
  const answered = questions.filter((_q, i) => answer(i)).length;
  const send = () => {
    const lines = questions.map((q, i) => (answer(i) ? `${q.question} — ${answer(i)}` : "")).filter(Boolean);
    if (lines.length) onSend(lines.join("\n"));
  };
  return (
    <div className="question-form" role="group" aria-label="Questions to answer">
      {questions.map((q, i) => (
        <fieldset key={`${i}-${q.question}`} className="question">
          <legend><b>{i + 1}.</b> {q.question}</legend>
          <div className="agent-choices">
            {q.options.map((o, j) => (
              <button key={o} className={`agent-choice ${!typing[i] && picked[i] === o ? "on" : ""}`} aria-pressed={!typing[i] && picked[i] === o}
                onClick={() => { setTyping((t) => ({ ...t, [i]: false })); setPicked((p) => ({ ...p, [i]: o })); }}>
                <b>{String.fromCharCode(97 + j)}</b> {o}{j === 0 ? <span className="hint"> (recommended)</span> : null}
              </button>
            ))}
            {typing[i]
              ? <input autoFocus className="question-own" placeholder="Your own answer" aria-label={`Your own answer to: ${q.question}`} value={own[i] ?? ""} onChange={(e) => setOwn((x) => ({ ...x, [i]: e.target.value }))} onKeyDown={(e) => { if (e.key === "Enter" && answered) send(); }} />
              : <button className="agent-choice" onClick={() => setTyping((t) => ({ ...t, [i]: true }))}><b>…</b> Other</button>}
          </div>
        </fieldset>
      ))}
      <div className="row">
        <button className="primary" disabled={!answered} onClick={send}>Send {answered === questions.length ? "answers" : `${answered} of ${questions.length} answers`}</button>
        {answered > 0 && answered < questions.length && <span className="hint">Unanswered questions are left open.</span>}
      </div>
    </div>
  );
}
