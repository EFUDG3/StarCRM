// Starbot chat tab: Copilot-style chat over the signed-in user's M365 data
// (email, calendar, SharePoint/OneDrive), plus the persistent to-do rail.
// Conversation state lives here (sessionStorage) — the server is stateless
// between turns and returns the updated history in each turn's `done` event.
import { useEffect, useRef, useState } from "react";
import {
  Send, Square, LogOut, Check, Trash2, Plus, ListTodo, Mail, Calendar,
  FolderSearch, Users, Sparkles, RotateCcw,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import * as api from "./api.js";

const INK = "#1C1C1C";
const MIST = "#F3F0EC";
const SEA = "#922525";
const TIDE = "#C0392B";

const CHAT_KEY = "starbot_chat_v1";

// Friendly labels for tool-activity chips while starbot works.
const TOOL_LABELS = {
  search_emails: { icon: Mail, label: "Searching email" },
  list_recent_emails: { icon: Mail, label: "Reading recent email" },
  read_email: { icon: Mail, label: "Reading an email" },
  list_calendar_events: { icon: Calendar, label: "Checking calendar" },
  search_files: { icon: FolderSearch, label: "Searching SharePoint/OneDrive" },
  list_todos: { icon: ListTodo, label: "Checking your to-do list" },
  add_todos: { icon: ListTodo, label: "Adding to-dos" },
  complete_todo: { icon: ListTodo, label: "Updating a to-do" },
  update_todo: { icon: ListTodo, label: "Updating a task" },
  delete_todo: { icon: ListTodo, label: "Removing a to-do" },
  search_crm_contacts: { icon: Users, label: "Searching CRM contacts" },
  get_crm_contact: { icon: Users, label: "Reading a CRM contact" },
};

const SUGGESTIONS = [
  "Make a to-do list from my recent emails",
  "Triage my inbox — what needs action?",
  "What's on my calendar this week?",
  "Summarize my unread email",
];

const loadChat = () => {
  try {
    const raw = sessionStorage.getItem(CHAT_KEY);
    return raw ? JSON.parse(raw) : { api: [], ui: [] };
  } catch {
    return { api: [], ui: [] };
  }
};

export default function StarbotChat() {
  const [me, setMe] = useState(null); // null = probing
  const [chat, setChat] = useState(loadChat); // { api: [...], ui: [...] }
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [todos, setTodos] = useState([]);
  const [todoText, setTodoText] = useState("");
  const abortRef = useRef(null);
  const scrollRef = useRef(null);

  useEffect(() => {
    api.authMe().then(setMe).catch(() => setMe({ signedIn: false, configured: true }));
  }, []);

  const refreshTodos = () =>
    api.listTodos().then(setTodos).catch(() => {});

  useEffect(() => {
    if (me?.signedIn) refreshTodos();
  }, [me?.signedIn]);

  useEffect(() => {
    try { sessionStorage.setItem(CHAT_KEY, JSON.stringify(chat)); } catch { /* full */ }
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [chat]);

  const send = async (textArg) => {
    const text = (textArg ?? input).trim();
    if (!text || busy) return;
    setInput("");
    setError("");
    setBusy(true);

    const apiMessages = [...chat.api, { role: "user", content: text }];
    // The streaming assistant turn: text accumulates, tool chips append.
    let ui = [...chat.ui, { role: "user", text }, { role: "assistant", text: "", tools: [] }];
    setChat({ api: apiMessages, ui });

    const patchLast = (fn) => {
      ui = [...ui.slice(0, -1), fn(ui[ui.length - 1])];
      setChat({ api: apiMessages, ui });
    };

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await api.chatStream(
        apiMessages,
        (evt) => {
          if (evt.type === "text") {
            patchLast((m) => ({ ...m, text: m.text + evt.text }));
          } else if (evt.type === "tool") {
            patchLast((m) => ({ ...m, tools: [...m.tools, evt.name] }));
          } else if (evt.type === "todos_changed") {
            refreshTodos();
          } else if (evt.type === "done") {
            setChat({ api: evt.messages, ui });
          } else if (evt.type === "error") {
            setError(evt.message);
          }
        },
        ctrl.signal
      );
    } catch (e) {
      if (e.name !== "AbortError") {
        setError(
          e.status === 401
            ? "Your Microsoft session expired — sign in again."
            : e.message || "Something went wrong."
        );
      }
    } finally {
      abortRef.current = null;
      setBusy(false);
    }
  };

  const stop = () => abortRef.current?.abort();

  const clearChat = () => {
    if (busy) return;
    setChat({ api: [], ui: [] });
    setError("");
  };

  const signOut = async () => {
    await api.authLogout().catch(() => {});
    sessionStorage.removeItem(CHAT_KEY);
    // Full reload so the app-level sign-in gate takes over everywhere.
    window.location.href = "/";
  };

  const addTodoManual = async () => {
    const text = todoText.trim();
    if (!text) return;
    setTodoText("");
    await api.addTodo(text).catch(() => {});
    refreshTodos();
  };

  const toggleTodo = async (t) => {
    await api.setTodoDone(t.id, !t.done).catch(() => {});
    refreshTodos();
  };

  const removeTodo = async (t) => {
    await api.deleteTodo(t.id).catch(() => {});
    refreshTodos();
  };

  // --- Gates ---------------------------------------------------------------
  if (me === null) {
    return (
      <div className="py-16 text-center font-mono text-sm tracking-widest uppercase" style={{ color: SEA }}>
        Checking sign-in…
      </div>
    );
  }

  if (!me.configured) {
    return (
      <Card>
        <Sparkles size={28} style={{ color: SEA }} className="mx-auto mb-3" />
        <h2 className="text-xl font-bold mb-2" style={{ fontFamily: "Georgia, serif" }}>Starbot isn't configured yet</h2>
        <p className="text-sm" style={{ color: "#4a5a60" }}>
          Microsoft 365 sign-in needs ENTRA_TENANT_ID, ENTRA_CLIENT_ID, and
          ENTRA_CLIENT_SECRET set on the server. Ask IT (Ethan) to finish setup.
        </p>
      </Card>
    );
  }

  if (!me.signedIn) {
    return (
      <Card>
        <Sparkles size={28} style={{ color: SEA }} className="mx-auto mb-3" />
        <h2 className="text-xl font-bold mb-2" style={{ fontFamily: "Georgia, serif" }}>
          <span style={{ color: SEA }}>★</span> Meet starbot
        </h2>
        <p className="text-sm mb-5 max-w-md mx-auto" style={{ color: "#4a5a60" }}>
          Your Star Flooring assistant. It reads <b>your</b> email, calendar, and
          SharePoint — only what you can already see — to answer questions, build
          to-do lists, triage your inbox, and draft replies.
        </p>
        <a
          href={api.authLoginUrl()}
          className="inline-flex items-center gap-2 px-5 py-2.5 rounded text-white text-sm font-medium"
          style={{ background: INK }}
        >
          {/* Microsoft logo */}
          <svg width="16" height="16" viewBox="0 0 21 21" aria-hidden="true">
            <rect x="1" y="1" width="9" height="9" fill="#f25022" />
            <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
            <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
            <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
          </svg>
          Sign in with Microsoft
        </a>
      </Card>
    );
  }

  // --- Signed in: chat + to-do rail ----------------------------------------
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_20rem] xl:grid-cols-[1fr_24rem] items-start">
      {/* Chat column */}
      <section className="bg-white rounded-lg border-l-4 flex flex-col" style={{ borderColor: SEA, height: "calc(100vh - 14rem)", minHeight: "28rem" }}>
        <div className="flex items-center justify-between px-4 py-2.5 border-b" style={{ borderColor: "#eee9e2" }}>
          <div className="font-mono text-xs uppercase tracking-widest" style={{ color: SEA }}>
            ★ Starbot · {me.name}
          </div>
          <div className="flex items-center gap-1">
            <button onClick={clearChat} title="New conversation" className="p-1.5 rounded hover:bg-stone-100" style={{ color: INK }}>
              <RotateCcw size={14} />
            </button>
            <button onClick={signOut} title="Sign out of Microsoft" className="p-1.5 rounded hover:bg-stone-100" style={{ color: INK }}>
              <LogOut size={14} />
            </button>
          </div>
        </div>

        <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {chat.ui.length === 0 && (
            <div className="pt-8 text-center">
              <div className="text-sm mb-4" style={{ color: "#8b9a9f" }}>
                Ask about your email, calendar, files, or contacts. A few ideas:
              </div>
              <div className="flex flex-wrap gap-2 justify-center max-w-md mx-auto">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="px-3 py-1.5 rounded-full text-xs font-medium bg-white hover:shadow-sm"
                    style={{ border: "1px solid #cdd6d4", color: INK }}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {chat.ui.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="flex justify-end">
                <div className="starbot-wrap max-w-[85%] rounded-lg px-3.5 py-2 text-[15px] text-white whitespace-pre-wrap" style={{ background: INK }}>
                  {m.text}
                </div>
              </div>
            ) : (
              <div key={i} className="flex justify-start">
                <div className="starbot-wrap max-w-[95%] min-w-0 text-[15px]">
                  {m.tools?.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mb-1.5">
                      {m.tools.map((name, j) => {
                        const t = TOOL_LABELS[name] || { icon: Sparkles, label: name };
                        const Icon = t.icon;
                        return (
                          <span key={j} className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full" style={{ background: MIST, color: SEA }}>
                            <Icon size={11} /> {t.label}
                          </span>
                        );
                      })}
                    </div>
                  )}
                  {m.text ? (
                    <div className="starbot-md rounded-lg px-3.5 py-2" style={{ background: MIST }}>
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          a: (props) => (
                            <a {...props} target="_blank" rel="noreferrer" style={{ color: SEA, textDecoration: "underline" }} />
                          ),
                        }}
                      >
                        {m.text}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    busy && i === chat.ui.length - 1 && (
                      <div className="font-mono text-xs px-1 py-1 animate-pulse" style={{ color: SEA }}>
                        thinking…
                      </div>
                    )
                  )}
                </div>
              </div>
            )
          )}
        </div>

        {error && (
          <div className="mx-4 mb-2 rounded p-2.5 text-xs" style={{ background: "#FBEAE8", color: TIDE, border: `1px solid ${TIDE}` }}>
            {error}
          </div>
        )}

        <div className="px-4 pb-3 pt-1 flex gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={1}
            placeholder="Ask starbot… (Enter to send, Shift+Enter for a new line)"
            className="flex-1 border rounded px-3 py-2 text-[15px] bg-white resize-none"
            style={{ borderColor: "#cdd6d4" }}
          />
          {busy ? (
            <button onClick={stop} title="Stop" className="px-3 rounded text-white" style={{ background: TIDE }}>
              <Square size={16} />
            </button>
          ) : (
            <button onClick={() => send()} title="Send" className="px-3 rounded text-white disabled:opacity-50" style={{ background: SEA }} disabled={!input.trim()}>
              <Send size={16} />
            </button>
          )}
        </div>
      </section>

      {/* To-do rail */}
      <aside className="bg-white rounded-lg border-l-4 p-4" style={{ borderColor: "#C8B89A" }}>
        <div className="flex items-center gap-2 mb-3">
          <ListTodo size={16} style={{ color: SEA }} />
          <span className="font-mono text-xs uppercase tracking-widest" style={{ color: SEA }}>To-do list</span>
        </div>
        <div className="flex gap-2 mb-3">
          <input
            value={todoText}
            onChange={(e) => setTodoText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addTodoManual()}
            placeholder="Add an item…"
            className="flex-1 border rounded px-2.5 py-1.5 text-sm bg-white min-w-0"
            style={{ borderColor: "#cdd6d4" }}
          />
          <button onClick={addTodoManual} className="px-2.5 rounded text-white shrink-0" style={{ background: INK }} title="Add">
            <Plus size={14} />
          </button>
        </div>
        <ul className="space-y-2 max-h-[24rem] overflow-y-auto pr-1">
          {todos.map((t) => (
            <li key={t.id} className="flex items-start gap-2 group">
              <button
                onClick={() => toggleTodo(t)}
                className="mt-0.5 w-4 h-4 rounded border flex items-center justify-center shrink-0"
                style={{ borderColor: t.done ? SEA : "#cdd6d4", background: t.done ? SEA : "white" }}
                title={t.done ? "Mark not done" : "Mark done"}
              >
                {t.done && <Check size={11} color="white" />}
              </button>
              <div className="min-w-0 flex-1">
                <div className="text-[15px] leading-snug" style={{ textDecoration: t.done ? "line-through" : "none", color: t.done ? "#8b9a9f" : INK }}>
                  {t.text}
                </div>
                <div className="starbot-wrap font-mono text-[10px] mt-0.5" style={{ color: "#8b9a9f" }}>
                  {t.priority && (
                    <span className="uppercase font-bold mr-1.5" style={{ color: t.priority === "high" ? TIDE : t.priority === "medium" ? "#B7791F" : "#4A5A6A" }}>
                      {t.priority}
                    </span>
                  )}
                  {t.status === "in_progress" && <span className="mr-1.5" style={{ color: SEA }}>in progress</span>}
                  {t.due && <span style={{ color: t.due < new Date().toISOString().slice(0, 10) && !t.done ? TIDE : undefined }}>due {t.due}</span>}
                  {t.due && t.source ? " · " : ""}
                  {t.source && (t.sourceLink
                    ? <a href={t.sourceLink} target="_blank" rel="noreferrer" className="underline" style={{ color: SEA }}>{t.source}</a>
                    : t.source)}
                </div>
              </div>
              <button onClick={() => removeTodo(t)} className="p-1 opacity-0 group-hover:opacity-100 rounded hover:bg-stone-100 shrink-0" style={{ color: TIDE }} title="Delete">
                <Trash2 size={12} />
              </button>
            </li>
          ))}
          {todos.length === 0 && (
            <li className="text-sm py-3 text-center" style={{ color: "#8b9a9f" }}>
              Nothing yet. Try "Make a to-do list from my recent emails".
            </li>
          )}
        </ul>
      </aside>
    </div>
  );
}

function Card({ children }) {
  return (
    <section className="bg-white rounded-lg p-8 text-center border-l-4 max-w-lg mx-auto mt-8" style={{ borderColor: SEA }}>
      {children}
    </section>
  );
}
