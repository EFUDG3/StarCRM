// Tasks tab: Asana-style board over the same todos the starbot chat manages.
// Three status columns (To do / In progress / Done) with drag-and-drop plus
// arrow buttons (mobile), and a click-to-cycle priority flag on each card.
// Data lives in the todos table; the chat's to-do rail shows the open subset.
import { useEffect, useState } from "react";
import {
  Plus, Trash2, Flag, ChevronLeft, ChevronRight, Sparkles, ListTodo,
} from "lucide-react";
import * as api from "./api.js";

const INK = "#1C1C1C";
const MIST = "#F3F0EC";
const SEA = "#922525";
const TIDE = "#C0392B";

const COLUMNS = [
  { key: "todo", label: "To do" },
  { key: "in_progress", label: "In progress" },
  { key: "done", label: "Done" },
];

// none → low → medium → high → none
const PRIORITY_CYCLE = { "": "low", low: "medium", medium: "high", high: "" };
const PRIORITY_STYLE = {
  high: { background: "#FBEAE8", color: TIDE },
  medium: { background: "#FDF3E3", color: "#B7791F" },
  low: { background: "#EEF2F1", color: "#4A5A6A" },
};

const todayISO = () => new Date().toISOString().slice(0, 10);

export default function TaskBoard() {
  const [me, setMe] = useState(null);
  const [todos, setTodos] = useState(null); // null = loading
  const [newText, setNewText] = useState("");
  const [dragId, setDragId] = useState(null);

  useEffect(() => {
    api.authMe().then(setMe).catch(() => setMe({ signedIn: false, configured: true }));
  }, []);

  const refresh = () =>
    api.listTodos(true).then(setTodos).catch(() => setTodos([]));

  useEffect(() => {
    if (me?.signedIn) refresh();
  }, [me?.signedIn]);

  // Optimistic updates: flip the UI immediately, then reconcile that one card
  // from the PATCH response. No full refetch on success — a refetch here caused
  // a visible settle/flicker a beat after every click and re-sorted cards
  // underneath the cursor.
  const patchLocal = (saved) =>
    setTodos((cur) => cur.map((x) => (x.id === saved.id ? saved : x)));

  const move = async (t, status) => {
    if (!status || t.status === status) return;
    setTodos((cur) => cur.map((x) => (x.id === t.id ? { ...x, status } : x)));
    try {
      patchLocal(await api.updateTodo(t.id, { status }));
    } catch {
      refresh(); // server said no — fall back to truth
    }
  };

  const cyclePriority = async (t) => {
    const priority = PRIORITY_CYCLE[t.priority || ""];
    setTodos((cur) => cur.map((x) => (x.id === t.id ? { ...x, priority } : x)));
    try {
      patchLocal(await api.updateTodo(t.id, { priority }));
    } catch {
      refresh();
    }
  };

  const addTask = async () => {
    const text = newText.trim();
    if (!text) return;
    setNewText("");
    await api.addTodo(text).catch(() => {});
    refresh();
  };

  const remove = async (t) => {
    await api.deleteTodo(t.id).catch(() => {});
    refresh();
  };

  const clearDone = async () => {
    const done = (todos || []).filter((t) => t.status === "done");
    if (done.length === 0) return;
    if (!window.confirm(`Delete ${done.length} completed task${done.length > 1 ? "s" : ""}?`)) return;
    await Promise.all(done.map((t) => api.deleteTodo(t.id).catch(() => {})));
    refresh();
  };

  if (me === null || (me?.signedIn && todos === null)) {
    return (
      <div className="py-16 text-center font-mono text-sm tracking-widest uppercase" style={{ color: SEA }}>
        Loading tasks…
      </div>
    );
  }

  if (!me.signedIn) {
    return (
      <section className="bg-white rounded-lg p-8 text-center border-l-4 max-w-lg mx-auto mt-8" style={{ borderColor: SEA }}>
        <ListTodo size={28} style={{ color: SEA }} className="mx-auto mb-3" />
        <h2 className="text-xl font-bold mb-2" style={{ fontFamily: "Georgia, serif" }}>Your task board</h2>
        <p className="text-sm mb-5" style={{ color: "#4a5a60" }}>
          Tasks are tied to your Microsoft account. Sign in to see your board.
        </p>
        <a
          href={api.authLoginUrl()}
          className="inline-flex items-center gap-2 px-5 py-2.5 rounded text-white text-sm font-medium"
          style={{ background: INK }}
        >
          <svg width="16" height="16" viewBox="0 0 21 21" aria-hidden="true">
            <rect x="1" y="1" width="9" height="9" fill="#f25022" />
            <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
            <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
            <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
          </svg>
          Sign in with Microsoft
        </a>
      </section>
    );
  }

  return (
    <div className="grid gap-4 md:grid-cols-3 items-start">
      {COLUMNS.map((col, colIdx) => {
        const cards = todos.filter((t) => t.status === col.key);
        return (
          <section
            key={col.key}
            className="bg-white rounded-lg p-3 min-h-[16rem] min-w-0"
            style={{ border: "1px solid #e5e0d8", outline: dragId ? `2px dashed ${SEA}22` : "none" }}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const t = todos.find((x) => x.id === dragId);
              if (t) move(t, col.key);
              setDragId(null);
            }}
          >
            <div className="flex items-center justify-between mb-3 px-1">
              <span className="font-mono text-xs uppercase tracking-widest font-bold" style={{ color: SEA }}>
                {col.label}
                <span className="ml-2 font-normal" style={{ color: "#8b9a9f" }}>{cards.length}</span>
              </span>
              {col.key === "done" && cards.length > 0 && (
                <button onClick={clearDone} className="font-mono text-[10px] uppercase tracking-wider px-2 py-1 rounded hover:bg-stone-100" style={{ color: TIDE }}>
                  Clear
                </button>
              )}
            </div>

            {col.key === "todo" && (
              <div className="flex gap-1.5 mb-3">
                <input
                  value={newText}
                  onChange={(e) => setNewText(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && addTask()}
                  placeholder="Add a task…"
                  className="flex-1 border rounded px-2.5 py-1.5 text-sm min-w-0"
                  style={{ borderColor: "#cdd6d4", background: MIST }}
                />
                <button onClick={addTask} className="px-2.5 rounded text-white shrink-0" style={{ background: INK }} title="Add task">
                  <Plus size={14} />
                </button>
              </div>
            )}

            <ul className="space-y-2">
              {cards.map((t) => {
                const overdue = t.due && t.status !== "done" && t.due < todayISO();
                return (
                  <li
                    key={t.id}
                    draggable
                    onDragStart={(e) => {
                      // A firm press on a button/link often moves the pointer a
                      // pixel, which starts a DRAG and swallows the CLICK (felt
                      // as "priority didn't change until I clicked again").
                      // Drags may only begin from the card body.
                      if (e.target.closest("button, a")) {
                        e.preventDefault();
                        return;
                      }
                      setDragId(t.id);
                    }}
                    onDragEnd={() => setDragId(null)}
                    className="group rounded p-3 border-l-4 cursor-grab active:cursor-grabbing"
                    style={{
                      background: MIST,
                      borderColor: t.priority === "high" ? TIDE : t.priority === "medium" ? "#B7791F" : "#C8B89A",
                      opacity: t.status === "done" ? 0.65 : 1,
                    }}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="starbot-wrap text-[15px] leading-snug min-w-0" style={{ textDecoration: t.status === "done" ? "line-through" : "none" }}>
                        {t.text}
                      </div>
                      <button onClick={() => remove(t)} title="Delete" className="p-1 rounded hover-reveal hover:bg-white shrink-0" style={{ color: TIDE }}>
                        <Trash2 size={12} />
                      </button>
                    </div>

                    <div className="flex items-center gap-2 mt-2 flex-wrap">
                      <button
                        onClick={() => cyclePriority(t)}
                        title="Change priority (click to cycle)"
                        className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full"
                        style={t.priority ? PRIORITY_STYLE[t.priority] : { background: "white", color: "#8b9a9f", border: "1px dashed #cdd6d4" }}
                      >
                        <Flag size={10} /> {t.priority || "priority"}
                      </button>
                      {t.due && (
                        <span className="font-mono text-[10px]" style={{ color: overdue ? TIDE : "#8b9a9f" }}>
                          {overdue ? "overdue " : "due "}{t.due}
                        </span>
                      )}
                      {t.source && (
                        <span className="font-mono text-[10px] truncate min-w-0 max-w-full" style={{ color: "#8b9a9f" }}>
                          {t.sourceLink ? (
                            <a href={t.sourceLink} target="_blank" rel="noreferrer" className="underline inline-flex items-center gap-0.5" style={{ color: SEA }}>
                              <Sparkles size={9} /> {t.source}
                            </a>
                          ) : t.source}
                        </span>
                      )}
                    </div>

                    <div className="flex justify-between mt-1.5 hover-reveal">
                      <button
                        onClick={() => move(t, COLUMNS[colIdx - 1]?.key)}
                        disabled={colIdx === 0}
                        className="p-0.5 rounded hover:bg-white disabled:opacity-0"
                        title={`Move to ${COLUMNS[colIdx - 1]?.label || ""}`}
                        style={{ color: INK }}
                      >
                        <ChevronLeft size={14} />
                      </button>
                      <button
                        onClick={() => move(t, COLUMNS[colIdx + 1]?.key)}
                        disabled={colIdx === COLUMNS.length - 1}
                        className="p-0.5 rounded hover:bg-white disabled:opacity-0"
                        title={`Move to ${COLUMNS[colIdx + 1]?.label || ""}`}
                        style={{ color: INK }}
                      >
                        <ChevronRight size={14} />
                      </button>
                    </div>
                  </li>
                );
              })}
              {cards.length === 0 && (
                <li className="text-xs text-center py-6 rounded" style={{ color: "#8b9a9f", border: "1px dashed #e5e0d8" }}>
                  {col.key === "todo" ? "Nothing here — add a task or ask starbot." : "Drag tasks here."}
                </li>
              )}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
