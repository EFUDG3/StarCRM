import { useState, useEffect, useMemo, useRef } from "react";
import {
  Plus, Search, Phone, Mail, X, Check, Clock, Pencil, Trash2, ChevronLeft, RotateCcw,
  UserCircle, ChevronDown, UserPlus, Camera, Sparkles, LayoutGrid, ListTodo, Car, Building2,
} from "lucide-react";
import * as api from "./api.js";
import StarbotChat from "./Chat.jsx";
import TaskBoard from "./Tasks.jsx";
import MileageTracker from "./Mileage.jsx";
import AccountsBoard from "./Accounts.jsx";

// ---------- Brand tokens ----------
// Star brand: red #922525, black, white, with warm supporting neutrals.
const INK = "#1C1C1C";   // near-black charcoal — body text, dark buttons, rule line
const MIST = "#F3F0EC";  // warm paper — page + chip backgrounds
const SEA = "#922525";   // Star brand red — primary accent (labels, links, icons)
const TIDE = "#C0392B";  // brighter alert red — overdue / urgent
const SAND = "#C8B89A";  // warm sand — soft borders / non-urgent accents
const ROW_LINE = "#e4dfd3"; // subtle warm table row separator (shared with the Accounts tab)

const CATEGORIES = {
  bd: { label: "Business Dev", color: SEA },          // brand red
  gc: { label: "GC", color: "#3E4C59" },              // slate
  vendor: { label: "Vendor", color: "#8C6D46" },      // bronze
  property: { label: "Property", color: "#6D3B47" },  // plum
  client: { label: "Client", color: "#2F5D50" },      // deep green
  sub: { label: "Subcontractor", color: "#4A5A6A" },  // blue-gray
  designer: { label: "Designer", color: "#7A5C8E" },  // muted purple
  insurance: { label: "Insurance", color: "#B7791F" },// amber
  healthcare: { label: "Healthcare", color: "#2C7A7B" }, // teal
  other: { label: "Other", color: "#6B7280" },        // neutral gray
};

// Display label for a contact's category — the custom text when it's "Other".
const catLabel = (c) =>
  c.category === "other" && c.categoryLabel
    ? c.categoryLabel
    : (CATEGORIES[c.category]?.label || "Other");
const catColor = (c) => (CATEGORIES[c.category] || CATEGORIES.other).color;

const todayISO = () => new Date().toISOString().slice(0, 10);

// Format an ISO date (YYYY-MM-DD) as "Jun 22, 2026" using local time (no UTC shift).
const fmtDate = (iso) => {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  if (!y) return iso;
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric",
  });
};

const blank = {
  name: "", company: "", role: "", email: "", phone: "", phones: [],
  category: "bd", categoryLabel: "", nextAction: "", nextDue: "", notes: "", log: [],
};

export default function StarCRM() {
  // Top-level tab: starbot chat, task board, or the CRM board. "#starbot" and
  // "#tasks" deep-link (the M365 sign-in redirect lands on #starbot).
  const [view, setView] = useState(() =>
    window.location.hash === "#starbot" ? "chat"
      : window.location.hash === "#tasks" ? "tasks"
      : window.location.hash === "#mileage" ? "mileage"
      : window.location.hash === "#accounts" ? "accounts"
      : "board"
  );
  // Company gate: the whole app requires a Microsoft sign-in (me.signedIn).
  // null = still checking. When M365 login isn't configured (local dev without
  // the client secret), the gate is skipped and the legacy profile model runs.
  const [me, setMe] = useState(() => api.getCachedMe() ?? null);
  const [contacts, setContacts] = useState(null); // null = loading
  const [users, setUsers] = useState([]);
  const [userId, setUserId] = useState(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [sortBy, setSortBy] = useState("due"); // due | created | name
  const [selectedId, setSelectedId] = useState(null);
  const [editing, setEditing] = useState(null); // contact object being edited, or "new"
  const [touchText, setTouchText] = useState("");
  const [saveState, setSaveState] = useState("idle"); // idle | saving | saved | error
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState("");
  const [cameraOpen, setCameraOpen] = useState(false);
  // Up-next rail collapse state, remembered across visits.
  const [upNextOpen, setUpNextOpen] = useState(() => localStorage.getItem("upNextOpen") !== "0");
  useEffect(() => { localStorage.setItem("upNextOpen", upNextOpen ? "1" : "0"); }, [upNextOpen]);
  const cardInputRef = useRef(null);

  const refresh = async () => {
    const data = await api.listContacts();
    setContacts(data);
    return data;
  };

  // Session probe runs first — everything else waits for it.
  useEffect(() => {
    if (me) return; // cached from a prior probe this session
    api.authMe().then(setMe).catch(() => setMe({ signedIn: false, configured: true }));
  }, []);

  const authed = me != null && (me.signedIn || !me.configured);

  // Once signed in: pick the active profile and load their board. Default is
  // the signed-in user's OWN profile (not the first row, which was Bob).
  useEffect(() => {
    if (!authed) return;
    (async () => {
      try {
        const us = await api.listUsers();
        let id = api.getCurrentUser();
        if (!us.find((u) => u.id === id)) {
          id = (me.userId && us.find((u) => u.id === me.userId)) ? me.userId : (us[0]?.id || null);
          api.setCurrentUser(id);
        }
        setUsers(us);
        setUserId(id);
        if (id) await refresh();
        else setContacts([]);
      } catch {
        setSaveState("error");
        setContacts([]);
      }
    })();
  }, [authed]);

  // Back button: when the add/edit form or a contact's detail is open, the
  // browser/phone Back button closes it and returns to the list instead of
  // leaving the app. We push a history entry while an overlay is open and pop
  // it on Back; if the overlay is closed via the UI instead, we consume the
  // entry so Back doesn't leave a dead press behind.
  const overlayOpen = editing !== null || selectedId !== null;
  const closedByBack = useRef(false);
  useEffect(() => {
    if (!overlayOpen) return;
    window.history.pushState({ starcrmOverlay: true }, "");
    const onPop = () => {
      closedByBack.current = true;
      setEditing(null);
      setSelectedId(null);
    };
    window.addEventListener("popstate", onPop);
    return () => {
      window.removeEventListener("popstate", onPop);
      if (!closedByBack.current) window.history.back();
      closedByBack.current = false;
    };
  }, [overlayOpen]);

  const reloadUsers = async () => {
    const us = await api.listUsers();
    setUsers(us);
    return us;
  };

  const switchUser = async (id) => {
    api.setCurrentUser(id);
    setUserId(id);
    setSelectedId(null);
    setEditing(null);
    setContacts(null);
    try {
      await refresh();
    } catch {
      setContacts([]);
    }
  };

  const addUser = async () => {
    const name = window.prompt("New user name:");
    if (!name || !name.trim()) return;
    const u = await api.createUser(name.trim());
    await reloadUsers();
    await switchUser(u.id);
  };

  const renameUser = async (id, currentName) => {
    const name = window.prompt("Rename user:", currentName);
    if (!name || !name.trim()) return;
    await api.renameUser(id, name.trim());
    await reloadUsers();
  };

  const removeUser = async (id) => {
    if (!window.confirm("Delete this user and ALL their contacts? This cannot be undone.")) return;
    await api.deleteUser(id);
    const us = await reloadUsers();
    if (id === userId) {
      const next = us[0]?.id || null;
      if (next) await switchUser(next);
      else {
        api.setCurrentUser(null);
        setUserId(null);
        setContacts([]);
      }
    }
  };

  // Wrap a mutating API call with the save-state indicator + refetch.
  const mutate = async (fn) => {
    setSaveState("saving");
    try {
      await fn();
      await refresh();
      setSaveState("saved");
      setTimeout(() => setSaveState("idle"), 1200);
    } catch {
      setSaveState("error");
    }
  };

  const resetData = async () => {
    if (!window.confirm("Reset to the original seeded contacts? Your changes will be lost.")) return;
    await mutate(() => api.resetData());
    setSelectedId(null);
  };

  const selected = contacts?.find((c) => c.id === selectedId) || null;

  const filtered = useMemo(() => {
    if (!contacts) return [];
    const q = query.trim().toLowerCase();
    return contacts
      .filter((c) => (filter === "all" ? true : c.category === filter))
      .filter((c) => !q || [c.name, c.company, c.role, c.notes].join(" ").toLowerCase().includes(q))
      .sort((a, b) => {
        if (sortBy === "created") {
          // Newest first by creation date; ties fall back to name.
          const ca = a.created || "", cb = b.created || "";
          if (ca !== cb) return ca < cb ? 1 : -1;
          return a.name.localeCompare(b.name);
        }
        if (sortBy === "name") return a.name.localeCompare(b.name);
        const da = a.nextDue || "9999", db = b.nextDue || "9999";
        if (da !== db) return da < db ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
  }, [contacts, query, filter, sortBy]);

  const dueSoon = useMemo(() => {
    if (!contacts) return [];
    const t = todayISO();
    return contacts
      .filter((c) => c.nextDue && c.nextAction)
      .sort((a, b) => (a.nextDue < b.nextDue ? -1 : 1))
      .slice(0, 4)
      .map((c) => ({ ...c, overdue: c.nextDue < t, today: c.nextDue === t }));
  }, [contacts]);

  const saveEdit = async (form) => {
    if (!form.name.trim()) return;
    const isNew = !form.id;
    // cardImage is a client-side preview from the scanner only — never persisted.
    const { cardImage, ...payload } = form;
    await mutate(() => (isNew ? api.createContact(payload) : api.updateContact(form.id, payload)));
    setEditing(null);
    if (isNew) setSelectedId(null);
  };

  const deleteContact = async (id) => {
    if (!window.confirm("Delete this contact?")) return;
    await mutate(() => api.deleteContact(id));
    setSelectedId(null);
  };

  const logTouch = async (id) => {
    if (!touchText.trim()) return;
    const note = touchText.trim();
    setTouchText("");
    await mutate(() => api.logTouch(id, note));
  };

  const completeAction = async (id) => {
    await mutate(() => api.completeAction(id));
  };

  // Scan a business card → extract fields → open the form prefilled for review.
  // Shared by the webcam capture (a Blob) and the file-picker fallback (a File).
  const runScan = async (file) => {
    if (!file) return;
    setScanError("");
    setScanning(true);
    try {
      const f = await api.scanCard(file);
      const gotSomething = ["name", "company", "role", "email", "phone"]
        .some((k) => (f[k] || "").trim());
      if (!gotSomething) {
        setScanError("Couldn't read any details off that card. Try a sharper, well-lit photo, or add the contact manually.");
        return;
      }
      setSelectedId(null);
      setEditing({
        ...blank,
        name: f.name || "",
        company: f.company || "",
        role: f.role || "",
        email: f.email || "",
        phone: f.phone || "",
        // Claude classifies the card and reads typed phone numbers; prefill both
        // (validated against known categories, else fall back to Business Dev).
        phones: (f.phones && f.phones.length) ? f.phones : (f.phone ? [{ type: "work", number: f.phone }] : []),
        category: (f.category && CATEGORIES[f.category]) ? f.category : "bd",
        cardImage: f.cardImage || null,
      });
    } catch (err) {
      const msg =
        err.status === 503 ? "Card scanning isn't configured yet (missing API key). Add the contact manually for now."
          : err.message === "timeout" ? "The scan took too long — the service may be busy. Try again in a moment."
          : (err.status === 502 || err.status === 529 || err.status === 429) ? "The scanning service is busy right now. Give it a few seconds and try again."
          : "Couldn't read that card. Try again, or add the contact manually.";
      setScanError(msg);
    } finally {
      setScanning(false);
    }
  };

  // File-picker fallback (webcam unavailable or permission blocked).
  const handleScanFile = (e) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-picking the same file
    runScan(file);
  };

  // Webcam capture → same scan pipeline as a picked file.
  const handleCameraCapture = async (blob) => {
    setCameraOpen(false);
    await runScan(blob);
  };

  const switchView = (v) => {
    setView(v);
    // Keep the hash in sync (deep link + where the sign-in redirect lands)
    // without pushing history entries that would fight the Back-button logic.
    const hash = v === "chat" ? "#starbot" : v === "tasks" ? "#tasks" : v === "mileage" ? "#mileage" : v === "accounts" ? "#accounts" : window.location.pathname;
    window.history.replaceState(null, "", hash);
  };

  if (me === null) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: MIST }}>
        <div className="font-mono text-sm tracking-widest uppercase" style={{ color: SEA }}>Checking sign-in…</div>
      </div>
    );
  }

  // The company gate: no Microsoft session, no app. Customers who stumble on
  // the URL see only this card; sign-in requires a Star tenant account.
  if (me.configured && !me.signedIn) {
    return (
      <div className="min-h-screen flex items-center justify-center px-4" style={{ background: MIST, color: INK }}>
        <section className="bg-white rounded-lg p-10 text-center border-l-4 max-w-md w-full shadow-sm" style={{ borderColor: SEA }}>
          <div className="text-5xl mb-3" style={{ color: SEA }}>★</div>
          <h1 className="text-3xl font-bold tracking-tight mb-1" style={{ fontFamily: "Georgia, serif" }}>Starbot</h1>
          <div className="font-mono text-xs tracking-[0.25em] uppercase mb-5" style={{ color: SEA }}>
            Star Flooring &amp; Remodeling
          </div>
          <p className="text-[15px] mb-6" style={{ color: "#4a5a60" }}>
            The internal assistant for the Star team: email triage, task boards,
            and the relationship CRM. Sign in with your company account.
          </p>
          <a
            href={api.authLoginUrl()}
            className="inline-flex items-center gap-2 px-6 py-3 rounded text-white text-base font-medium"
            style={{ background: INK }}
          >
            <svg width="18" height="18" viewBox="0 0 21 21" aria-hidden="true">
              <rect x="1" y="1" width="9" height="9" fill="#f25022" />
              <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
              <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
              <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
            </svg>
            Sign in with Microsoft
          </a>
          <div className="font-mono text-[11px] mt-5" style={{ color: "#8b9a9f" }}>
            Star Flooring company accounts only
          </div>
        </section>
      </div>
    );
  }

  if (!contacts && view === "board") {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: MIST }}>
        <div className="font-mono text-sm tracking-widest uppercase" style={{ color: SEA }}>Loading contacts…</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen" style={{ background: MIST, color: INK }}>
      {/* All tabs share one container width so the page boundaries don't jump
          when switching views (board previously kept a tighter column). */}
      <div className="max-w-5xl lg:max-w-[calc(64rem+(100vw-64rem)/2)] mx-auto px-4 py-6">

        {/* Header */}
        <header className="mb-6 border-b-2 pb-4" style={{ borderColor: INK }}>
          {/* Row 1: title + board actions. The action buttons live ABOVE the tab
              bar so a growing tab list never competes with them for width. */}
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <div className="font-mono text-xs tracking-[0.25em] uppercase mb-1 whitespace-nowrap" style={{ color: SEA }}>
                {view === "chat" ? "AI assistant" : view === "tasks" ? "Task board" : view === "mileage" ? "Mileage tracker" : view === "accounts" ? "Shared hit list" : "Relationships"}
              </div>
              <h1 className="text-3xl font-bold tracking-tight whitespace-nowrap" style={{ fontFamily: "Georgia, serif" }}>
                <span style={{ color: SEA }}>★</span> {view === "chat" ? "Starbot" : view === "tasks" ? "Star Tasks" : view === "mileage" ? "Star Mileage" : view === "accounts" ? "Star Accounts" : "Star CRM"}
              </h1>
            </div>
            {view === "board" && (
              <div className="flex items-center flex-wrap gap-2">
                <span className="font-mono text-xs" style={{ color: saveState === "error" ? TIDE : SEA }}>
                  {saveState === "saving" ? "saving…" : saveState === "saved" ? "saved ✓" : saveState === "error" ? "save failed" : ""}
                </span>
                <button onClick={resetData} title="Reset data" className="p-2 rounded hover:bg-white" style={{ color: INK }}>
                  <RotateCcw size={16} />
                </button>
                <input ref={cardInputRef} type="file" accept="image/*" onChange={handleScanFile} className="hidden" />
                <button
                  onClick={() => { setScanError(""); setCameraOpen(true); }}
                  disabled={scanning}
                  title="Scan a business card with the camera"
                  className="flex items-center gap-1.5 px-3 py-2 rounded text-sm font-medium"
                  style={{ background: "white", color: INK, border: "1px solid #cdd6d4", opacity: scanning ? 0.6 : 1 }}
                >
                  <Camera size={16} /> {scanning ? "Reading…" : "Scan card"}
                </button>
                <button
                  onClick={() => setEditing({ ...blank })}
                  className="flex items-center gap-1.5 px-3 py-2 rounded text-white text-sm font-medium"
                  style={{ background: INK }}
                >
                  <Plus size={16} /> Add contact
                </button>
              </div>
            )}
          </div>

          {/* Row 2: the tab bar (+ profile switcher on the CRM board) */}
          <div className="flex flex-wrap items-center gap-3 mt-4">
            <div className="flex gap-0.5 p-0.5 rounded bg-white" style={{ border: "1px solid #cdd6d4" }}>
              <button onClick={() => switchView("chat")} className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded whitespace-nowrap" style={view === "chat" ? { background: INK, color: "white" } : { background: "white", color: INK }}>
                <Sparkles size={14} /> Starbot
              </button>
              <button onClick={() => switchView("tasks")} className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded whitespace-nowrap" style={view === "tasks" ? { background: INK, color: "white" } : { background: "white", color: INK }}>
                <ListTodo size={14} /> Tasks
              </button>
              <button onClick={() => switchView("mileage")} className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded whitespace-nowrap" style={view === "mileage" ? { background: INK, color: "white" } : { background: "white", color: INK }}>
                <Car size={14} /> Mileage
              </button>
              <button onClick={() => switchView("board")} className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded whitespace-nowrap" style={view === "board" ? { background: INK, color: "white" } : { background: "white", color: INK }}>
                <LayoutGrid size={14} /> CRM
              </button>
              <button onClick={() => switchView("accounts")} className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded whitespace-nowrap" style={view === "accounts" ? { background: INK, color: "white" } : { background: "white", color: INK }}>
                <Building2 size={14} /> Accounts
              </button>
            </div>
            {view === "board" && (
              <UserSwitcher
                users={users}
                userId={userId}
                onSwitch={switchUser}
                onAdd={addUser}
                onRename={renameUser}
                onDelete={removeUser}
              />
            )}
          </div>
        </header>

        {/* Starbot chat tab */}
        {view === "chat" && <StarbotChat />}

        {/* Task board tab */}
        {view === "tasks" && <TaskBoard />}

        {/* Mileage tab */}
        {view === "mileage" && <MileageTracker />}

        {/* Accounts tab (shared hit list) */}
        {view === "accounts" && <AccountsBoard />}

        {/* Webcam card capture (opens from the Scan card button) */}
        {cameraOpen && (
          <CardCamera
            onCapture={handleCameraCapture}
            onClose={() => setCameraOpen(false)}
            onUseFile={() => { setCameraOpen(false); cardInputRef.current?.click(); }}
          />
        )}

        {view === "board" && <>
        {/* Scan error banner */}
        {scanError && (
          <div className="mb-4 rounded p-3 text-sm flex items-start justify-between gap-3" style={{ background: "#FBEAE8", color: TIDE, border: `1px solid ${TIDE}` }}>
            <span>{scanError}</span>
            <button onClick={() => setScanError("")} className="shrink-0" title="Dismiss"><X size={16} /></button>
          </div>
        )}

        {/* Up next rail — sized to match the contact list rows; collapsible so
            the full CRM list can be reviewed without the rail taking space. */}
        {dueSoon.length > 0 && !selected && !editing && (
          <section className="mb-6">
            <button
              onClick={() => setUpNextOpen((o) => !o)}
              className="flex items-center gap-1.5 font-mono text-sm font-bold tracking-[0.2em] uppercase mb-3"
              style={{ color: SEA }}
              title={upNextOpen ? "Collapse" : "Expand"}
            >
              Up next ({dueSoon.length})
              <ChevronDown size={14} className={"transition-transform " + (upNextOpen ? "" : "-rotate-90")} />
            </button>
            {upNextOpen && (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {dueSoon.map((c) => (
                <button
                  key={c.id}
                  onClick={() => setSelectedId(c.id)}
                  className="text-left bg-white rounded p-3 flex items-start gap-3 border-l-4 hover:shadow-sm transition-shadow min-w-0"
                  style={{ borderColor: c.overdue || c.today ? TIDE : SAND }}
                >
                  <Clock size={15} className="mt-1 shrink-0" style={{ color: c.overdue || c.today ? TIDE : SEA }} />
                  <div className="min-w-0">
                    <div className="font-semibold truncate">{c.name}</div>
                    <div className="text-sm truncate" style={{ color: "#4a5a60" }}>{c.nextAction}</div>
                    <div className="font-mono text-xs mt-1 font-medium" style={{ color: c.overdue ? TIDE : "#6b7a80" }}>
                      {c.overdue ? "overdue · " : c.today ? "today · " : "due "}{c.nextDue}
                    </div>
                  </div>
                </button>
              ))}
            </div>
            )}
          </section>
        )}

        {/* Edit / Add form */}
        {editing && <ContactForm initial={editing} onCancel={() => setEditing(null)} onSave={saveEdit} />}

        {/* Detail view */}
        {selected && !editing && (
          <section className="bg-white rounded-lg p-5 mb-6 border-l-4" style={{ borderColor: catColor(selected) }}>
            <button onClick={() => setSelectedId(null)} className="flex items-center gap-1 font-mono text-xs uppercase tracking-widest mb-3" style={{ color: SEA }}>
              <ChevronLeft size={14} /> All contacts
            </button>
            <div className="flex items-start justify-between gap-3 flex-wrap">
              <div>
                <h2 className="text-2xl font-bold" style={{ fontFamily: "Georgia, serif" }}>{selected.name}</h2>
                <div className="text-sm" style={{ color: "#4a5a60" }}>{selected.company}{selected.role ? " · " + selected.role : ""}</div>
                {selected.created && (
                  <div className="font-mono text-[11px] mt-1" style={{ color: "#8b9a9f" }}>Added {fmtDate(selected.created)}</div>
                )}
                <div className="flex gap-3 mt-2 flex-wrap">
                  {selected.email && <a href={"mailto:" + selected.email} className="flex items-center gap-1 text-sm underline" style={{ color: SEA }}><Mail size={14} />{selected.email}</a>}
                  {(selected.phones || []).map((p, i) => (
                    <span key={i} className="flex items-center gap-1 text-sm">
                      <Phone size={14} style={{ color: SEA }} />{p.number}
                      {p.type ? <span className="text-xs" style={{ color: "#8b9a9f" }}>· {p.type.charAt(0).toUpperCase() + p.type.slice(1)}</span> : null}
                    </span>
                  ))}
                </div>
              </div>
              <div className="flex gap-2">
                <button onClick={() => setEditing({ ...selected })} className="p-2 rounded hover:bg-stone-100"><Pencil size={16} /></button>
                <button onClick={() => deleteContact(selected.id)} className="p-2 rounded hover:bg-stone-100" style={{ color: TIDE }}><Trash2 size={16} /></button>
              </div>
            </div>

            {selected.nextAction && (
              <div className="mt-4 rounded p-3 flex items-start justify-between gap-3" style={{ background: MIST }}>
                <div>
                  <div className="font-mono text-xs uppercase tracking-widest mb-0.5" style={{ color: SEA }}>Next action{selected.nextDue ? " · " + selected.nextDue : ""}</div>
                  <div className="text-sm font-medium">{selected.nextAction}</div>
                </div>
                <button onClick={() => completeAction(selected.id)} className="flex items-center gap-1 px-2.5 py-1.5 rounded text-white text-xs font-medium shrink-0" style={{ background: SEA }}>
                  <Check size={14} /> Done
                </button>
              </div>
            )}

            {selected.notes && <p className="mt-4 text-sm leading-relaxed whitespace-pre-wrap">{selected.notes}</p>}

            <div className="mt-5">
              <div className="font-mono text-xs uppercase tracking-widest mb-2" style={{ color: INK }}>Log note</div>
              <div className="flex gap-2">
                <input
                  value={touchText}
                  onChange={(e) => setTouchText(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && logTouch(selected.id)}
                  placeholder="Called re: 4620 walkthrough…"
                  className="flex-1 border rounded px-3 py-2 text-sm bg-white"
                  style={{ borderColor: "#cdd6d4" }}
                />
                <button onClick={() => logTouch(selected.id)} className="px-3 py-2 rounded text-white text-sm font-medium" style={{ background: INK }}>Log</button>
              </div>
              <ul className="mt-3 space-y-2">
                {selected.log.map((l, i) => (
                  <li key={i} className="flex gap-3 text-sm">
                    <span className="font-mono text-xs pt-0.5 shrink-0" style={{ color: SEA }}>{l.date}</span>
                    <span>{l.note}</span>
                  </li>
                ))}
                {selected.log.length === 0 && <li className="text-sm" style={{ color: "#8b9a9f" }}>No notes yet. Add the first one above.</li>}
              </ul>
            </div>
          </section>
        )}

        {/* List view */}
        {!selected && !editing && (
          <>
            <div className="flex gap-2 mb-4 flex-wrap items-center">
              <div className="relative flex-1 min-w-48">
                <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "#8b9a9f" }} />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search name, company, notes"
                  className="w-full bg-white border rounded pl-9 pr-3 py-2 text-sm"
                  style={{ borderColor: "#cdd6d4" }}
                />
              </div>
              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value)}
                className="bg-white border rounded px-2 py-2 font-mono text-xs uppercase tracking-wider shrink-0"
                style={{ borderColor: "#cdd6d4", color: INK }}
                title="Sort contacts"
              >
                <option value="due">Sort: Due date</option>
                <option value="created">Sort: Newest added</option>
                <option value="name">Sort: Name</option>
              </select>
              {["all", ...Object.keys(CATEGORIES)].map((k) => (
                <button
                  key={k}
                  onClick={() => setFilter(k)}
                  className="font-mono text-xs uppercase tracking-wider px-3 py-2 rounded"
                  style={filter === k ? { background: INK, color: "white" } : { background: "white", color: INK }}
                >
                  {k === "all" ? "All" : CATEGORIES[k].label}
                </button>
              ))}
            </div>

            <div className="bg-white rounded-lg border overflow-x-auto" style={{ borderColor: "#cdd6d4" }}>
              <table className="w-full text-sm" style={{ minWidth: 720 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid #cdd6d4" }}>
                    {["Name", "Company", "Role", "Email", "Phone", "Next action", "Category"].map((h) => (
                      <th key={h} className="px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-left" style={{ color: "#6b7a80" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((c) => {
                    const overdue = c.nextDue && c.nextDue < todayISO();
                    return (
                      <tr key={c.id} onClick={() => setSelectedId(c.id)} className="cursor-pointer hover:bg-stone-50" style={{ borderBottom: "1px solid " + ROW_LINE }}>
                        <td className="px-3 py-2.5 align-top" style={{ borderLeft: "3px solid " + catColor(c) }}>
                          <div className="font-semibold break-words" style={{ color: INK, maxWidth: "18rem" }}>{c.name}</div>
                        </td>
                        <td className="px-3 py-2.5">{c.company ? <div className="truncate" style={{ color: "#4a5a60", maxWidth: "12rem" }}>{c.company}</div> : <span style={{ color: "#b0b8ba" }}>—</span>}</td>
                        <td className="px-3 py-2.5">{c.role ? <div className="truncate" style={{ color: "#4a5a60", maxWidth: "9rem" }}>{c.role}</div> : <span style={{ color: "#b0b8ba" }}>—</span>}</td>
                        <td className="px-3 py-2.5">{c.email ? <a href={"mailto:" + c.email} onClick={(e) => e.stopPropagation()} className="underline truncate align-bottom" style={{ color: SEA, maxWidth: "14rem", display: "inline-block" }}>{c.email}</a> : <span style={{ color: "#b0b8ba" }}>—</span>}</td>
                        <td className="px-3 py-2.5 font-mono text-[12px] whitespace-nowrap" style={{ color: "#4a5a60" }}>
                          {c.phone ? <>{c.phone}{(c.phones && c.phones.length > 1) ? <span style={{ color: "#b0b8ba" }}> +{c.phones.length - 1}</span> : null}</> : <span style={{ color: "#b0b8ba" }}>—</span>}
                        </td>
                        <td className="px-3 py-2.5 align-top">
                          {c.nextAction
                            ? <div className="text-[13px] line-clamp-2" style={{ color: overdue ? TIDE : "#4a5a60", maxWidth: "22rem" }}>{c.nextAction}{c.nextDue ? <span className="font-mono text-[11px]" style={{ color: overdue ? TIDE : "#8b9a9f" }}> ({c.nextDue})</span> : null}</div>
                            : <span style={{ color: "#b0b8ba" }}>—</span>}
                        </td>
                        <td className="px-3 py-2.5">
                          <span className="font-mono text-[10px] uppercase tracking-wider px-2 py-0.5 rounded whitespace-nowrap" style={{ background: MIST, color: catColor(c) }}>{catLabel(c)}</span>
                        </td>
                      </tr>
                    );
                  })}
                  {filtered.length === 0 && (
                    <tr><td colSpan={7} className="text-sm text-center py-8" style={{ color: "#8b9a9f" }}>No contacts match. Clear the search or add a new contact.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
        </>}
      </div>
    </div>
  );
}

function ContactForm({ initial, onCancel, onSave }) {
  // Always keep at least one phone row so there's a field to type into (empty
  // rows are dropped server-side on save).
  const [form, setForm] = useState(() => ({
    ...initial,
    phones: (initial.phones && initial.phones.length) ? initial.phones : [{ type: "cell", number: "" }],
  }));
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const setPhoneField = (i, k) => (e) => {
    const next = form.phones.slice();
    next[i] = { ...next[i], [k]: e.target.value };
    setForm({ ...form, phones: next });
  };
  const addPhone = () => setForm({ ...form, phones: [...form.phones, { type: "cell", number: "" }] });
  const removePhone = (i) => setForm({ ...form, phones: form.phones.filter((_, j) => j !== i) });
  const field = "w-full bg-white border rounded px-3 py-2 text-sm";
  const bc = { borderColor: "#cdd6d4" };
  const cap = "font-mono text-[10px] uppercase tracking-widest block mb-1";
  return (
    <section className="bg-white rounded-lg p-5 mb-6 border-l-4" style={{ borderColor: SEA }}>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold" style={{ fontFamily: "Georgia, serif" }}>{form.id ? "Edit contact" : "New contact"}</h2>
        <button onClick={onCancel} className="p-2 rounded hover:bg-stone-100"><X size={16} /></button>
      </div>
      {form.cardImage && (
        <div className="mb-4">
          <img src={form.cardImage} alt="Scanned card" className="max-h-40 rounded border" style={{ borderColor: "#cdd6d4" }} />
          <div className="font-mono text-[10px] uppercase tracking-widest mt-1" style={{ color: SEA }}>
            Scanned card · fields prefilled below, edit before saving
          </div>
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className={cap} style={{ color: SEA }}>Name *</span>
          <input className={field} style={bc} placeholder="Jane Doe" value={form.name} onChange={set("name")} />
        </label>
        <label className="block">
          <span className={cap} style={{ color: SEA }}>Company</span>
          <input className={field} style={bc} placeholder="Acme Co" value={form.company} onChange={set("company")} />
        </label>
        <label className="block">
          <span className={cap} style={{ color: SEA }}>Role</span>
          <input className={field} style={bc} placeholder="Project Manager" value={form.role} onChange={set("role")} />
        </label>
        <label className="block">
          <span className={cap} style={{ color: SEA }}>Category</span>
          <select className={field} style={bc} value={form.category} onChange={set("category")}>
            {Object.entries(CATEGORIES).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
        </label>
        {form.category === "other" && (
          <label className="block">
            <span className={cap} style={{ color: SEA }}>Specify category</span>
            <input className={field} style={bc} placeholder="e.g. Inspector, Lender" value={form.categoryLabel || ""} onChange={set("categoryLabel")} />
          </label>
        )}
        <label className="block">
          <span className={cap} style={{ color: SEA }}>Email</span>
          <input className={field} style={bc} placeholder="jane@acme.com" value={form.email} onChange={set("email")} />
        </label>
        <div className="block sm:col-span-2">
          <span className={cap} style={{ color: SEA }}>Phone numbers</span>
          <div className="space-y-2">
            {form.phones.map((p, i) => (
              <div key={i} className="flex gap-2">
                <select className="border rounded px-2 py-2 text-sm bg-white shrink-0" style={bc} value={p.type || "cell"} onChange={setPhoneField(i, "type")}>
                  <option value="cell">Cell</option>
                  <option value="work">Work</option>
                  <option value="home">Home</option>
                  <option value="other">Other</option>
                </select>
                <input className={field} style={bc} maxLength={40} autoComplete="off" placeholder="(619) 555-0100" value={p.number} onChange={setPhoneField(i, "number")} />
                <button type="button" onClick={() => removePhone(i)} className="px-2 rounded hover:bg-stone-100 shrink-0" style={{ color: TIDE }} title="Remove"><X size={15} /></button>
              </div>
            ))}
            <button type="button" onClick={addPhone} className="text-xs font-medium flex items-center gap-1" style={{ color: SEA }}><Plus size={13} /> Add phone number</button>
          </div>
        </div>
        <label className="block">
          <span className={cap} style={{ color: SEA }}>Next action</span>
          <input className={field} style={bc} placeholder="Call re: estimate" value={form.nextAction} onChange={set("nextAction")} />
        </label>
        <label className="block">
          <span className={cap} style={{ color: SEA }}>Follow-up date</span>
          <input className={field} style={bc} type="date" value={form.nextDue} onChange={set("nextDue")} />
        </label>
      </div>
      <label className="block mt-3">
        <span className={cap} style={{ color: SEA }}>Notes</span>
        <textarea className={field + " h-24"} style={bc} placeholder="Context, preferences, history…" value={form.notes} onChange={set("notes")} />
      </label>
      <div className="flex gap-2 mt-4">
        <button onClick={() => onSave(form)} className="px-4 py-2 rounded text-white text-sm font-medium" style={{ background: INK }}>Save contact</button>
        <button onClick={onCancel} className="px-4 py-2 rounded text-sm" style={{ background: MIST }}>Cancel</button>
      </div>
    </section>
  );
}

function UserSwitcher({ users, userId, onSwitch, onAdd, onRename, onDelete }) {
  const [open, setOpen] = useState(false);
  const current = users.find((u) => u.id === userId);
  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium bg-white"
        style={{ color: INK, border: "1px solid #cdd6d4" }}
      >
        <UserCircle size={16} style={{ color: SEA }} />
        <span className="max-w-[10rem] truncate">{current ? current.name : "Select user"}</span>
        <ChevronDown size={14} />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute left-0 mt-1 w-64 bg-white rounded-lg shadow-lg z-20 py-1" style={{ border: "1px solid #cdd6d4" }}>
            <div className="font-mono text-[10px] uppercase tracking-widest px-3 py-1.5" style={{ color: "#8b9a9f" }}>Switch user</div>
            {users.map((u) => (
              <div key={u.id} className="flex items-center group">
                <button
                  onClick={() => { onSwitch(u.id); setOpen(false); }}
                  className="flex-1 text-left px-3 py-2 text-sm hover:bg-stone-50 flex items-center gap-2 min-w-0"
                >
                  {u.id === userId ? <Check size={14} style={{ color: SEA }} /> : <span style={{ width: 14, display: "inline-block" }} />}
                  <span className="truncate">{u.name}</span>
                </button>
                <button onClick={() => onRename(u.id, u.name)} title="Rename" className="p-1.5 hover-reveal hover:bg-stone-100 rounded">
                  <Pencil size={13} />
                </button>
                <button onClick={() => onDelete(u.id)} title="Delete" className="p-1.5 mr-1 hover-reveal hover:bg-stone-100 rounded" style={{ color: TIDE }}>
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
            <div className="border-t mt-1 pt-1" style={{ borderColor: "#eef2f1" }}>
              <button onClick={() => { onAdd(); setOpen(false); }} className="w-full text-left px-3 py-2 text-sm flex items-center gap-2 hover:bg-stone-50" style={{ color: SEA }}>
                <UserPlus size={14} /> Add user
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// Live webcam capture for business cards. Prefers the rear camera on phones
// (facingMode "environment"); on desktop the browser just uses the default cam.
// The captured frame is a JPEG Blob handed to the same scan endpoint as a picked
// file — nothing is written to disk. If the camera is missing or blocked, the
// user is routed to the file-picker fallback instead.
function CardCamera({ onCapture, onClose, onUseFile }) {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const shotRef = useRef(null);          // the captured Blob
  const [preview, setPreview] = useState(""); // object URL of the frozen frame
  const [ready, setReady] = useState(false);  // stream is live, capture allowed
  const [err, setErr] = useState("");
  const [mirror, setMirror] = useState(false); // front/desktop cam → mirror the live preview

  useEffect(() => {
    let cancelled = false;
    if (!navigator.mediaDevices?.getUserMedia) {
      setErr("This browser can't open the camera here. Upload a file instead.");
      return;
    }
    (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "environment", width: { ideal: 1920 }, height: { ideal: 1080 } },
          audio: false,
        });
        if (cancelled) { stream.getTracks().forEach((t) => t.stop()); return; }
        streamRef.current = stream;
        // Mirror the LIVE preview only for a front-facing (desktop/webcam) camera
        // so the operator frames the card naturally — moving it left moves it left
        // on screen. The mobile rear camera (facingMode "environment") is left as-is.
        // The CAPTURE is un-mirrored (snap() draws the raw frame), so card text is
        // never reversed.
        const facing = stream.getVideoTracks()[0]?.getSettings?.().facingMode;
        if (!cancelled) setMirror(facing !== "environment");
        const v = videoRef.current;
        if (v) {
          v.srcObject = stream;
          v.onloadedmetadata = () => { if (!cancelled) setReady(true); };
          v.play().then(() => { if (!cancelled) setReady(true); }).catch(() => {});
        }
      } catch (e) {
        setErr(
          e?.name === "NotAllowedError"
            ? "Camera access is blocked. Allow it in the browser address bar, or upload a file instead."
            : e?.name === "NotFoundError"
              ? "No camera found on this device. Upload a file instead."
              : "Couldn't start the camera. Upload a file instead."
        );
      }
    })();
    return () => {
      cancelled = true;
      if (streamRef.current) streamRef.current.getTracks().forEach((t) => t.stop());
    };
  }, []);

  // Release the frozen-frame object URL when it changes or on unmount.
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  const stopStream = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
  };

  const snap = () => {
    const v = videoRef.current;
    if (!v || !v.videoWidth) return;
    const canvas = document.createElement("canvas");
    canvas.width = v.videoWidth;
    canvas.height = v.videoHeight;
    canvas.getContext("2d").drawImage(v, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((blob) => {
      if (!blob) return;
      shotRef.current = blob;
      setPreview(URL.createObjectURL(blob));
    }, "image/jpeg", 0.9);
  };

  const retake = () => { shotRef.current = null; setPreview(""); };
  const useShot = () => { stopStream(); onCapture(shotRef.current); };
  const cancel = () => { stopStream(); onClose(); };
  const useFile = () => { stopStream(); onUseFile(); };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.6)" }}
      onClick={cancel}
    >
      <div className="bg-white rounded-lg p-4 max-w-lg w-full" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2 font-mono text-xs uppercase tracking-widest" style={{ color: SEA }}>
            <Camera size={14} /> Scan a card
          </div>
          <button onClick={cancel} className="p-1.5 rounded hover:bg-stone-100" title="Close"><X size={16} /></button>
        </div>

        {err ? (
          <div className="text-sm p-4 rounded" style={{ background: "#FBEAE8", color: TIDE, border: `1px solid ${TIDE}` }}>
            {err}
          </div>
        ) : (
          <div className="relative rounded overflow-hidden" style={{ background: "#000", aspectRatio: "4 / 3" }}>
            <video
              ref={videoRef}
              playsInline
              muted
              autoPlay
              className="w-full h-full object-cover"
              style={{ display: preview ? "none" : "block", transform: mirror ? "scaleX(-1)" : "none" }}
            />
            {preview && (
              <img src={preview} alt="Captured card" className="w-full h-full object-contain" style={{ background: "#000" }} />
            )}
            {!preview && (
              <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                <div style={{ width: "82%", aspectRatio: "1.75 / 1", border: "2px dashed rgba(255,255,255,0.9)", borderRadius: 8 }} />
              </div>
            )}
          </div>
        )}

        <div className="flex items-center gap-2 mt-3">
          {err ? (
            <>
              <button onClick={useFile} className="px-4 py-2 rounded text-white text-sm font-medium" style={{ background: INK }}>Upload a file</button>
              <button onClick={cancel} className="px-4 py-2 rounded text-sm" style={{ background: MIST }}>Cancel</button>
            </>
          ) : preview ? (
            <>
              <button onClick={useShot} className="flex items-center gap-1.5 px-4 py-2 rounded text-white text-sm font-medium" style={{ background: SEA }}>
                <Check size={16} /> Use this photo
              </button>
              <button onClick={retake} className="flex items-center gap-1.5 px-4 py-2 rounded text-sm" style={{ background: MIST }}>
                <RotateCcw size={14} /> Retake
              </button>
            </>
          ) : (
            <>
              <button onClick={snap} disabled={!ready} className="flex items-center gap-1.5 px-4 py-2 rounded text-white text-sm font-medium disabled:opacity-50" style={{ background: SEA }}>
                <Camera size={16} /> {ready ? "Capture" : "Starting camera…"}
              </button>
              <button onClick={useFile} className="px-4 py-2 rounded text-sm" style={{ background: MIST }}>Upload a file instead</button>
            </>
          )}
        </div>

        {!err && !preview && (
          <div className="text-xs mt-2" style={{ color: "#8b9a9f" }}>
            Hold the card inside the frame, filling as much of it as you can, then Capture.
          </div>
        )}
      </div>
    </div>
  );
}

