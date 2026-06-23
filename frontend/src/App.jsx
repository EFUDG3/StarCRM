import { useState, useEffect, useMemo, useRef } from "react";
import {
  Plus, Search, Phone, Mail, X, Check, Clock, Pencil, Trash2, ChevronLeft, RotateCcw,
  UserCircle, ChevronDown, UserPlus, Camera,
} from "lucide-react";
import * as api from "./api.js";

// ---------- Brand tokens ----------
// Star brand: red #922525, black, white, with warm supporting neutrals.
const INK = "#1C1C1C";   // near-black charcoal — body text, dark buttons, rule line
const MIST = "#F3F0EC";  // warm paper — page + chip backgrounds
const SEA = "#922525";   // Star brand red — primary accent (labels, links, icons)
const TIDE = "#C0392B";  // brighter alert red — overdue / urgent
const SAND = "#C8B89A";  // warm sand — soft borders / non-urgent accents

const CATEGORIES = {
  bd: { label: "Business Dev", color: SEA },          // brand red
  gc: { label: "GC", color: "#3E4C59" },              // slate
  vendor: { label: "Vendor", color: "#8C6D46" },      // bronze
  property: { label: "Property", color: "#6D3B47" },  // plum
  client: { label: "Client", color: "#2F5D50" },      // deep green
  sub: { label: "Subcontractor", color: "#4A5A6A" },  // blue-gray
  designer: { label: "Designer", color: "#7A5C8E" },  // muted purple
  insurance: { label: "Insurance", color: "#B7791F" },// amber
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
  name: "", company: "", role: "", email: "", phone: "",
  category: "bd", categoryLabel: "", nextAction: "", nextDue: "", notes: "", log: [],
};

export default function StarCRM() {
  const [contacts, setContacts] = useState(null); // null = loading
  const [users, setUsers] = useState([]);
  const [userId, setUserId] = useState(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [selectedId, setSelectedId] = useState(null);
  const [editing, setEditing] = useState(null); // contact object being edited, or "new"
  const [touchText, setTouchText] = useState("");
  const [saveState, setSaveState] = useState("idle"); // idle | saving | saved | error
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState("");
  const cardInputRef = useRef(null);

  const refresh = async () => {
    const data = await api.listContacts();
    setContacts(data);
    return data;
  };

  // Initial load: pick the active user (stored or first), then load their board.
  useEffect(() => {
    (async () => {
      try {
        const us = await api.listUsers();
        let id = api.getCurrentUser();
        if (!us.find((u) => u.id === id)) {
          id = us[0]?.id || null;
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
  }, []);

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
        const da = a.nextDue || "9999", db = b.nextDue || "9999";
        if (da !== db) return da < db ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
  }, [contacts, query, filter]);

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
    await mutate(() => (isNew ? api.createContact(form) : api.updateContact(form.id, form)));
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
  const handleScanFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-picking the same file
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

  if (!contacts) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: MIST }}>
        <div className="font-mono text-sm tracking-widest uppercase" style={{ color: SEA }}>Loading the board…</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen" style={{ background: MIST, color: INK }}>
      <div className="max-w-5xl mx-auto px-4 py-6">

        {/* Header */}
        <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between mb-6 border-b-2 pb-4" style={{ borderColor: INK }}>
          <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-end sm:gap-4">
            <div>
              <div className="font-mono text-xs tracking-[0.25em] uppercase mb-1" style={{ color: SEA }}>Relationship board</div>
              <h1 className="text-3xl font-bold tracking-tight" style={{ fontFamily: "Georgia, serif" }}>
                <span style={{ color: SEA }}>★</span> Star CRM
              </h1>
            </div>
            <UserSwitcher
              users={users}
              userId={userId}
              onSwitch={switchUser}
              onAdd={addUser}
              onRename={renameUser}
              onDelete={removeUser}
            />
          </div>
          <div className="flex items-center flex-wrap gap-2">
            <span className="font-mono text-xs" style={{ color: saveState === "error" ? TIDE : SEA }}>
              {saveState === "saving" ? "saving…" : saveState === "saved" ? "saved ✓" : saveState === "error" ? "save failed" : ""}
            </span>
            <button onClick={resetData} title="Reset data" className="p-2 rounded hover:bg-white" style={{ color: INK }}>
              <RotateCcw size={16} />
            </button>
            <input
              ref={cardInputRef}
              type="file"
              accept="image/*"
              capture="environment"
              onChange={handleScanFile}
              className="hidden"
            />
            <button
              onClick={() => cardInputRef.current?.click()}
              disabled={scanning}
              title="Scan a business card"
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
        </header>

        {/* Scan error banner */}
        {scanError && (
          <div className="mb-4 rounded p-3 text-sm flex items-start justify-between gap-3" style={{ background: "#FBEAE8", color: TIDE, border: `1px solid ${TIDE}` }}>
            <span>{scanError}</span>
            <button onClick={() => setScanError("")} className="shrink-0" title="Dismiss"><X size={16} /></button>
          </div>
        )}

        {/* Up next rail */}
        {dueSoon.length > 0 && !selected && !editing && (
          <section className="mb-6">
            <div className="font-mono text-lg font-bold tracking-[0.2em] uppercase mb-3" style={{ color: SEA }}>Up next</div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {dueSoon.map((c) => (
                <button
                  key={c.id}
                  onClick={() => setSelectedId(c.id)}
                  className="text-left bg-white rounded p-3 flex items-start gap-3 border-l-4 hover:shadow-sm transition-shadow min-w-0"
                  style={{ borderColor: c.overdue || c.today ? TIDE : SAND }}
                >
                  <Clock size={20} className="mt-0.5 shrink-0" style={{ color: c.overdue || c.today ? TIDE : SEA }} />
                  <div className="min-w-0">
                    <div className="text-lg font-semibold truncate">{c.name}</div>
                    <div className="text-base truncate" style={{ color: "#4a5a60" }}>{c.nextAction}</div>
                    <div className="font-mono text-sm mt-1 font-medium" style={{ color: c.overdue ? TIDE : "#6b7a80" }}>
                      {c.overdue ? "overdue · " : c.today ? "today · " : "due "}{c.nextDue}
                    </div>
                  </div>
                </button>
              ))}
            </div>
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
                  {selected.phone && <span className="flex items-center gap-1 text-sm"><Phone size={14} style={{ color: SEA }} />{selected.phone}</span>}
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

            {selected.hasCard && <CardImage contactId={selected.id} />}

            <div className="mt-5">
              <div className="font-mono text-xs uppercase tracking-widest mb-2" style={{ color: INK }}>Log a touch</div>
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
                {selected.log.length === 0 && <li className="text-sm" style={{ color: "#8b9a9f" }}>No touches logged yet. Add the first one above.</li>}
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

            <ul className="space-y-2">
              {filtered.map((c) => {
                const overdue = c.nextDue && c.nextDue < todayISO();
                return (
                  <li key={c.id}>
                    <button
                      onClick={() => setSelectedId(c.id)}
                      className="w-full text-left bg-white rounded p-3 flex items-center gap-3 border-l-4 hover:shadow-sm transition-shadow"
                      style={{ borderColor: catColor(c) }}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-baseline gap-2 flex-wrap">
                          <span className="font-semibold">{c.name}</span>
                          <span className="text-sm" style={{ color: "#4a5a60" }}>{c.company}</span>
                        </div>
                        {c.nextAction && (
                          <div className="text-sm truncate mt-0.5" style={{ color: overdue ? TIDE : "#6b7a80" }}>
                            → {c.nextAction}{c.nextDue ? " (" + c.nextDue + ")" : ""}
                          </div>
                        )}
                      </div>
                      <span className="font-mono text-[10px] uppercase tracking-wider px-2 py-1 rounded shrink-0" style={{ background: MIST, color: catColor(c) }}>
                        {catLabel(c)}
                      </span>
                    </button>
                  </li>
                );
              })}
              {filtered.length === 0 && (
                <li className="text-sm text-center py-8" style={{ color: "#8b9a9f" }}>No contacts match. Clear the search or add a new contact.</li>
              )}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}

function ContactForm({ initial, onCancel, onSave }) {
  const [form, setForm] = useState(initial);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
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
        <label className="block">
          <span className={cap} style={{ color: SEA }}>Phone</span>
          <input className={field} style={bc} placeholder="(619) 555-0100" value={form.phone} onChange={set("phone")} />
        </label>
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
                <button onClick={() => onRename(u.id, u.name)} title="Rename" className="p-1.5 opacity-0 group-hover:opacity-100 hover:bg-stone-100 rounded">
                  <Pencil size={13} />
                </button>
                <button onClick={() => onDelete(u.id)} title="Delete" className="p-1.5 mr-1 opacity-0 group-hover:opacity-100 hover:bg-stone-100 rounded" style={{ color: TIDE }}>
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

function CardImage({ contactId }) {
  const [url, setUrl] = useState(null);
  useEffect(() => {
    let active = true;
    let made = null;
    api.fetchCardBlobUrl(contactId).then((u) => {
      if (active && u) { setUrl(u); made = u; }
      else if (u) URL.revokeObjectURL(u);
    });
    return () => {
      active = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [contactId]);
  if (!url) return null;
  return (
    <div className="mt-4">
      <div className="font-mono text-xs uppercase tracking-widest mb-2" style={{ color: INK }}>Business card</div>
      <img src={url} alt="Business card" className="max-h-56 rounded border" style={{ borderColor: "#cdd6d4" }} />
    </div>
  );
}
