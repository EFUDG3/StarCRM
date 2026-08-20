// Tasks tab: a read-only "today" aggregator, not a manual checklist. It pulls
// the signed-in user's own M365 + CRM data into three lanes — today's meetings
// (calendar), replies needed (Outlook flagged emails), and people to follow up
// (CRM next-actions) — then the personal/starbot to-do list runs full-width
// underneath. Nothing here writes to M365: replies link out to Outlook and drop
// off the next Refresh once the flag is cleared there (or when dismissed here).
import { useEffect, useRef, useState } from "react";
import {
  Calendar, Mail, Users, RotateCcw, Plus, Check, Trash2, ExternalLink,
  ChevronDown, ListTodo, Sparkles, X, RefreshCw,
} from "lucide-react";
import * as api from "./api.js";

const INK = "#1C1C1C";
const MIST = "#F3F0EC";
const SEA = "#922525";
const TIDE = "#C0392B";
const SAND = "#C8B89A";
const BORDER = "#cdd6d4";
const MEET = "#3E4C59";
const PERSON = "#2F5D50";

const CATEGORY = {
  bd: { label: "Business Dev", color: "#922525" },
  gc: { label: "GC", color: "#3E4C59" },
  vendor: { label: "Vendor", color: "#8C6D46" },
  property: { label: "Property", color: "#6D3B47" },
  client: { label: "Client", color: "#2F5D50" },
  sub: { label: "Subcontractor", color: "#4A5A6A" },
  designer: { label: "Designer", color: "#7A5C8E" },
  insurance: { label: "Insurance", color: "#B7791F" },
  healthcare: { label: "Healthcare", color: "#2C7A7B" },
  other: { label: "Other", color: "#6B7280" },
};
const catOf = (k) => CATEGORY[k] || CATEGORY.other;

const todayISO = () => new Date().toISOString().slice(0, 10);
const fmt12 = (hhmm) => {
  if (!hhmm) return "";
  let [h, m] = hhmm.split(":").map(Number);
  const ap = h < 12 ? "a" : "p";
  h = h % 12 || 12;
  return `${h}:${String(m || 0).padStart(2, "0")}${ap}`;
};
const meetTime = (m) => (m.isAllDay ? "all day" : fmt12((m.start || "").slice(11, 16)));
const wkday = (iso) => {
  const d = new Date((iso || "").slice(0, 10) + "T00:00");
  return isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { weekday: "short" });
};
const dueMeta = (due) => {
  if (!due) return { label: "", cls: "" };
  const t = todayISO();
  if (due < t) return { label: "overdue · " + due, cls: "over" };
  if (due === t) return { label: "due today", cls: "today" };
  return { label: "due " + due, cls: "" };
};
const fromName = (s) => (s || "").split("<")[0].replace(/"/g, "").trim() || s || "";
const shortDate = (iso) => {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};
const clockTime = (d) => d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

export default function TaskBoard() {
  const [me, setMe] = useState(() => api.getCachedMe() ?? null);
  const [agenda, setAgenda] = useState(null); // null = loading
  const [todos, setTodos] = useState([]);     // includes done (split below)
  const [busy, setBusy] = useState(false);
  const [synced, setSynced] = useState(null);
  const [err, setErr] = useState("");
  const [todoText, setTodoText] = useState("");
  const [showEarlier, setShowEarlier] = useState(false);
  const [showLater, setShowLater] = useState(false);
  const [showDone, setShowDone] = useState(false);
  const [undo, setUndo] = useState(null); // a just-deleted todo, for the undo toast
  const undoTimer = useRef(null);

  useEffect(() => {
    if (me) return;
    api.authMe().then(setMe).catch(() => setMe({ signedIn: false, configured: true }));
  }, []);

  const loadTodos = () => api.listTodos(true).then(setTodos).catch(() => {});

  const load = async () => {
    setBusy(true);
    setErr("");
    try {
      const [a, t] = await Promise.all([api.taskAgenda(), api.listTodos(true).catch(() => [])]);
      setAgenda(a);
      setTodos(t || []);
      setSynced(new Date());
    } catch (e) {
      setErr(e.status === 401 ? "Your Microsoft session expired — sign in again." : "Couldn't load your day. Try Refresh.");
      if (!agenda) setAgenda({ today: todayISO(), meetings: [], replies: [], followups: [] });
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => { if (me?.signedIn) load(); }, [me?.signedIn]);

  // --- Gates ---------------------------------------------------------------
  if (me === null) {
    return <div className="py-16 text-center font-mono text-sm tracking-widest uppercase" style={{ color: SEA }}>Checking sign-in…</div>;
  }
  if (!me.signedIn) {
    return (
      <section className="bg-white rounded-lg p-8 text-center border-l-4 max-w-lg mx-auto mt-8" style={{ borderColor: SEA }}>
        <ListTodo size={28} style={{ color: SEA }} className="mx-auto mb-3" />
        <h2 className="text-xl font-bold mb-2" style={{ fontFamily: "Georgia, serif" }}>Your day</h2>
        <p className="text-sm mb-5" style={{ color: "#4a5a60" }}>Your meetings, flagged emails, and follow-ups, assembled from your Microsoft account. Sign in to see them.</p>
        <a href={api.authLoginUrl()} className="inline-flex items-center gap-2 px-5 py-2.5 rounded text-white text-sm font-medium" style={{ background: INK }}>Sign in with Microsoft</a>
      </section>
    );
  }
  if (agenda === null) {
    return <div className="py-16 text-center font-mono text-sm tracking-widest uppercase" style={{ color: SEA }}>Loading your day…</div>;
  }

  const today = agenda.today;
  const meetings = agenda.meetings || [];
  const replies = agenda.replies || [];
  const followups = agenda.followups || [];
  const now = new Date();
  const isPast = (m) => { const d = new Date(m.end || m.start); return !isNaN(d.getTime()) && d < now; };
  const todays = meetings.filter((m) => (m.start || "").slice(0, 10) === today);
  const later = meetings.filter((m) => (m.start || "").slice(0, 10) > today);
  const upcoming = todays.filter((m) => !isPast(m));
  const earlier = todays.filter(isPast);
  const openTodos = todos.filter((t) => !t.done);
  const doneTodos = todos.filter((t) => t.done);

  const dateLabel = new Date(today + "T00:00").toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });

  const dismiss = async (id) => {
    setAgenda((a) => ({ ...a, replies: a.replies.filter((r) => r.id !== id) }));
    await api.dismissReply(id).catch(() => {});
  };
  const complete = async (id) => {
    setAgenda((a) => ({ ...a, followups: a.followups.filter((f) => f.id !== id) }));
    await api.completeFollowup(id).catch(() => {});
  };
  const addTodo = async () => {
    const t = todoText.trim();
    if (!t) return;
    setTodoText("");
    await api.addTodo(t).catch(() => {});
    loadTodos();
  };
  const markDone = async (td, done) => {
    setTodos((cur) => cur.map((x) => (x.id === td.id ? { ...x, done, status: done ? "done" : "todo" } : x)));
    await api.setTodoDone(td.id, done).catch(() => loadTodos());
  };
  const delTodo = async (td) => {
    setTodos((cur) => cur.filter((x) => x.id !== td.id));
    setUndo(td);
    if (undoTimer.current) clearTimeout(undoTimer.current);
    undoTimer.current = setTimeout(() => setUndo(null), 6000);
    await api.deleteTodo(td.id).catch(() => {});
  };
  const undoDelete = async () => {
    const td = undo;
    setUndo(null);
    if (undoTimer.current) clearTimeout(undoTimer.current);
    if (!td) return;
    await api.addTodo(td.text, td.due || "", td.priority || "").catch(() => {});
    loadTodos();
  };
  const clearDone = async () => {
    if (doneTodos.length === 0) return;
    if (!window.confirm(`Permanently delete ${doneTodos.length} completed task${doneTodos.length > 1 ? "s" : ""}?`)) return;
    setTodos((cur) => cur.filter((t) => !t.done));
    await Promise.all(doneTodos.map((t) => api.deleteTodo(t.id).catch(() => {})));
    loadTodos();
  };

  const MeetingRow = ({ m, dim }) => (
    <div className="flex items-start gap-3 py-2" style={{ borderTop: "1px solid #f0ece5", opacity: dim ? 0.55 : 1 }}>
      <div className="w-14 shrink-0 font-mono text-[12px] font-bold pt-0.5" style={{ color: MEET }}>{meetTime(m)}</div>
      <div className="min-w-0 flex-1">
        <div className="text-[14px] font-medium break-words">{m.subject || "(no title)"}</div>
        <div className="flex items-center gap-2 flex-wrap mt-0.5 text-[12px]" style={{ color: "#8b9a9f" }}>
          {m.location && <span className="truncate max-w-[16rem]">{m.location}</span>}
          {m.webLink && <a href={m.webLink} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 underline" style={{ color: SEA }}>open</a>}
        </div>
      </div>
    </div>
  );

  const TodoRow = ({ t, done }) => (
    <li className="flex items-start gap-2 group rounded px-1 py-0.5">
      <button
        onClick={() => markDone(t, !done)}
        title={done ? "Reopen task" : "Mark done"}
        className="mt-0.5 w-[18px] h-[18px] rounded border flex items-center justify-center shrink-0 hover:bg-stone-50"
        style={{ borderColor: done ? SEA : BORDER, background: done ? SEA : "white" }}
      >
        {done && <Check size={11} color="white" />}
      </button>
      <div className="min-w-0 flex-1">
        <div className="text-[15px] leading-snug break-words" style={{ color: done ? "#8b9a9f" : INK, textDecoration: done ? "line-through" : "none" }}>{t.text}</div>
        {(t.due || t.source) && (
          <div className="starbot-wrap font-mono text-[10px] mt-0.5" style={{ color: "#8b9a9f" }}>
            {t.due && <span style={{ color: !done && t.due < todayISO() ? TIDE : undefined }}>due {t.due}</span>}
            {t.due && t.source ? " · " : ""}
            {t.source && (t.sourceLink
              ? <a href={t.sourceLink} target="_blank" rel="noreferrer" className="underline inline-flex items-center gap-0.5" style={{ color: SEA }}><Sparkles size={9} /> {t.source}</a>
              : t.source)}
          </div>
        )}
      </div>
      <button onClick={() => delTodo(t)} className="p-1 hover-reveal rounded hover:bg-stone-100 shrink-0" style={{ color: TIDE }} title="Delete"><Trash2 size={12} /></button>
    </li>
  );

  return (
    <div className="relative">
      {/* Day bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
        <div>
          <div className="text-lg font-bold" style={{ fontFamily: "Georgia, serif" }}>{dateLabel}</div>
          <div className="flex gap-4 mt-1 text-[12.5px]" style={{ color: "#6b7a80" }}>
            <span className="inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-full" style={{ background: MEET }} /><b style={{ color: INK }}>{todays.length}</b> meeting{todays.length === 1 ? "" : "s"}</span>
            <span className="inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-full" style={{ background: SEA }} /><b style={{ color: INK }}>{replies.length}</b> repl{replies.length === 1 ? "y" : "ies"}</span>
            <span className="inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-full" style={{ background: PERSON }} /><b style={{ color: INK }}>{followups.length}</b> follow-up{followups.length === 1 ? "" : "s"}</span>
          </div>
        </div>
        <div className="flex items-center gap-2.5">
          {synced && <span className="text-[12px]" style={{ color: "#8b9a9f" }}>Updated {clockTime(synced)}</span>}
          <button onClick={load} disabled={busy} className="inline-flex items-center gap-1.5 px-3 py-2 rounded text-sm font-medium disabled:opacity-60" style={{ background: "white", color: INK, border: "1px solid " + BORDER }}>
            <RotateCcw size={15} className={busy ? "animate-spin" : ""} /> Refresh
          </button>
        </div>
      </div>

      {err && <div className="mb-4 rounded p-2.5 text-xs" style={{ background: "#FBEAE8", color: TIDE, border: `1px solid ${TIDE}` }}>{err}</div>}

      <div className="space-y-4">
        {/* Meetings */}
        <section className="bg-white rounded-lg border-l-4 p-4" style={{ borderColor: MEET }}>
          <div className="flex items-center gap-2 mb-1">
            <Calendar size={15} style={{ color: MEET }} />
            <span className="font-mono text-xs uppercase tracking-widest font-bold" style={{ color: MEET }}>Today's meetings</span>
            <span className="ml-auto text-[11px]" style={{ color: "#8b9a9f" }}>from your calendar</span>
          </div>
          {upcoming.length === 0 && earlier.length === 0 && <div className="text-sm py-2" style={{ color: "#8b9a9f" }}>Nothing on the calendar today.</div>}
          {upcoming.map((m) => <MeetingRow key={m.id} m={m} />)}
          {earlier.length > 0 && (
            <div className="mt-1">
              <button onClick={() => setShowEarlier((v) => !v)} className="inline-flex items-center gap-1 text-[12px] font-medium py-1" style={{ color: "#8b9a9f" }}>
                <ChevronDown size={13} className={"transition-transform " + (showEarlier ? "" : "-rotate-90")} /> Earlier today ({earlier.length})
              </button>
              {showEarlier && earlier.map((m) => <MeetingRow key={m.id} m={m} dim />)}
            </div>
          )}
          {later.length > 0 && (
            <div className="mt-2 pt-2" style={{ borderTop: "1px solid #f0ece5" }}>
              <button onClick={() => setShowLater((v) => !v)} className="inline-flex items-center gap-1 text-[12px] font-medium py-0.5" style={{ color: SEA }}>
                <ChevronDown size={13} className={"transition-transform " + (showLater ? "" : "-rotate-90")} /> Coming up this week ({later.length})
              </button>
              {showLater && (
                <ul className="mt-1 space-y-1">
                  {later.map((m) => (
                    <li key={m.id} className="text-[13px] flex gap-2" style={{ color: "#4a5a60" }}>
                      <span className="font-mono text-[11px] shrink-0 w-24" style={{ color: "#8b9a9f" }}>{wkday(m.start)} {meetTime(m)}</span>
                      <span className="truncate">{m.subject || "(no title)"}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </section>

        {/* Replies needed */}
        <section className="bg-white rounded-lg border-l-4 p-4" style={{ borderColor: SEA }}>
          <div className="flex items-center gap-2 mb-1">
            <Mail size={15} style={{ color: SEA }} />
            <span className="font-mono text-xs uppercase tracking-widest font-bold" style={{ color: SEA }}>Replies needed</span>
            <span className="ml-auto text-[11px]" style={{ color: "#8b9a9f" }}>emails you flagged in Outlook</span>
          </div>
          {replies.length === 0 && <div className="text-sm py-2" style={{ color: "#8b9a9f" }}>No flagged emails. Flag one in Outlook and it shows up here.</div>}
          {replies.map((r) => (
            <div key={r.id} className="flex items-start gap-3 py-2.5" style={{ borderTop: "1px solid #f0ece5" }}>
              <div className="min-w-0 flex-1">
                <div className="text-[14px] font-medium break-words">{r.subject || "(no subject)"}</div>
                <div className="flex items-center gap-2 flex-wrap mt-0.5 text-[12px]" style={{ color: "#8b9a9f" }}>
                  <span>{fromName(r.from)}</span>
                  {r.received && <span>· flagged, rec'd {shortDate(r.received)}</span>}
                </div>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                {r.webLink && (
                  <a href={r.webLink} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded text-[12px] font-medium" style={{ color: SEA, border: "1px solid " + BORDER }}>
                    <ExternalLink size={12} /> Open in Outlook
                  </a>
                )}
                <button onClick={() => dismiss(r.id)} title="Dismiss (remove from this list)" className="p-1.5 rounded hover:bg-stone-100" style={{ color: "#b0b8ba" }}><X size={15} /></button>
              </div>
            </div>
          ))}
        </section>

        {/* People to follow up */}
        <section className="bg-white rounded-lg border-l-4 p-4" style={{ borderColor: PERSON }}>
          <div className="flex items-center gap-2 mb-1">
            <Users size={15} style={{ color: PERSON }} />
            <span className="font-mono text-xs uppercase tracking-widest font-bold" style={{ color: PERSON }}>People to follow up</span>
            <span className="ml-auto text-[11px]" style={{ color: "#8b9a9f" }}>next actions from your CRM</span>
          </div>
          {followups.length === 0 && <div className="text-sm py-2" style={{ color: "#8b9a9f" }}>No open follow-ups. Set a "next action" on a contact and it lands here.</div>}
          {followups.map((f) => {
            const d = dueMeta(f.nextDue);
            const c = catOf(f.category);
            return (
              <div key={f.id} className="flex items-start gap-3 py-2.5" style={{ borderTop: "1px solid #f0ece5" }}>
                <button onClick={() => complete(f.id)} title="Mark done (logs it on the contact)" className="mt-0.5 w-[18px] h-[18px] rounded-full border flex items-center justify-center shrink-0 hover:bg-stone-50" style={{ borderColor: BORDER }}>
                  <Check size={11} style={{ color: "#c9d0ce" }} />
                </button>
                <div className="min-w-0 flex-1">
                  <div className="text-[14px]">{f.nextAction}</div>
                  <div className="flex items-center gap-2 flex-wrap mt-0.5">
                    <span className="font-mono text-[10px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: MIST, color: c.color }}>{f.name}</span>
                    {f.company && <span className="text-[12px]" style={{ color: "#8b9a9f" }}>{f.company}</span>}
                    {d.label && <span className="text-[11.5px] font-semibold" style={{ color: d.cls === "over" ? TIDE : d.cls === "today" ? SEA : "#8b9a9f" }}>{d.label}</span>}
                  </div>
                </div>
              </div>
            );
          })}
        </section>

        {/* Your tasks — personal / starbot list, full-width under the lanes */}
        <section className="bg-white rounded-lg border-l-4 p-4" style={{ borderColor: SAND }}>
          <div className="flex items-center gap-2 mb-3">
            <ListTodo size={16} style={{ color: SEA }} />
            <span className="font-mono text-xs uppercase tracking-widest" style={{ color: SEA }}>Your tasks</span>
            <span className="text-[11px]" style={{ color: "#8b9a9f" }}>{openTodos.length} open</span>
            <span className="ml-auto text-[11px]" style={{ color: "#8b9a9f" }}>personal + starbot to-dos</span>
          </div>
          <div className="flex gap-2 mb-3 max-w-2xl">
            <input value={todoText} onChange={(e) => setTodoText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addTodo()} placeholder="Add a task…" className="flex-1 border rounded px-3 py-2 text-sm bg-white min-w-0" style={{ borderColor: BORDER }} />
            <button onClick={addTodo} className="px-3 rounded text-white shrink-0" style={{ background: INK }} title="Add"><Plus size={16} /></button>
          </div>

          {openTodos.length === 0 && (
            <div className="text-sm py-3" style={{ color: "#8b9a9f" }}>Nothing open. Add one, or ask starbot to build a list from your email.</div>
          )}
          <ul className="grid gap-x-8 gap-y-1 md:grid-cols-2">
            {openTodos.map((t) => <TodoRow key={t.id} t={t} done={false} />)}
          </ul>

          {doneTodos.length > 0 && (
            <div className="mt-4 pt-3" style={{ borderTop: "1px solid #f0ece5" }}>
              <div className="flex items-center gap-2">
                <button onClick={() => setShowDone((v) => !v)} className="inline-flex items-center gap-1 font-mono text-[11px] uppercase tracking-widest font-bold" style={{ color: "#8b9a9f" }}>
                  <ChevronDown size={13} className={"transition-transform " + (showDone ? "" : "-rotate-90")} /> Completed ({doneTodos.length})
                </button>
                {showDone && (
                  <button onClick={clearDone} className="ml-auto font-mono text-[10px] uppercase tracking-wider px-2 py-1 rounded hover:bg-stone-100" style={{ color: TIDE }}>Clear completed</button>
                )}
              </div>
              {showDone && (
                <ul className="grid gap-x-8 gap-y-1 md:grid-cols-2 mt-2">
                  {doneTodos.map((t) => <TodoRow key={t.id} t={t} done />)}
                </ul>
              )}
              {!showDone && <div className="text-[11px] mt-1" style={{ color: "#b0b8ba" }}>Checked something off by accident? Expand to reopen it.</div>}
            </div>
          )}
        </section>
      </div>

      {/* Undo toast for an accidental delete */}
      {undo && (
        <div className="fixed left-1/2 -translate-x-1/2 bottom-6 z-50 flex items-center gap-3 px-4 py-2.5 rounded-lg shadow-lg text-sm" style={{ background: INK, color: "white" }}>
          <span>Task deleted</span>
          <button onClick={undoDelete} className="inline-flex items-center gap-1 font-semibold" style={{ color: "#f0c9c0" }}><RefreshCw size={13} /> Undo</button>
          <button onClick={() => setUndo(null)} className="opacity-70 hover:opacity-100"><X size={14} /></button>
        </div>
      )}
    </div>
  );
}
