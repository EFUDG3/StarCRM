// Accounts tab: the shared "hit list". Company-wide sales prospecting records
// every signed-in Star user can see and edit. Table view ranked by units, with
// saved-view filters, an assigned rep, a status pill, and a detail/edit form
// that holds multiple addresses/emails plus a customer-contact sub-list.
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Plus, Search, X, Trash2, ChevronLeft, Check, Building2, Globe, Phone,
  Mail, UserPlus, ArrowUpDown, ExternalLink, Pencil,
} from "lucide-react";
import * as api from "./api.js";

const INK = "#1C1C1C";
const MIST = "#F3F0EC";
const SEA = "#922525";
const TIDE = "#C0392B";
const BORDER = "#cdd6d4";
const ROW_LINE = "#e4dfd3"; // subtle warm row separator (shared with the CRM tab)

// Status: label + a soft-tinted pill. Order doubles as the pipeline sequence.
const STATUS = {
  prospect:  { label: "Prospect",  bg: "#ece7df", fg: "#6b7a80" },
  contacted: { label: "Contacted", bg: "rgba(62,76,89,.14)",  fg: "#3E4C59" },
  active:    { label: "Active",    bg: "rgba(47,93,80,.16)",  fg: "#2b6a58" },
  sold:      { label: "Sold",      bg: "#2F5D50", fg: "#ffffff" },
  dead:      { label: "Dead",      bg: "#e7e2da", fg: "#9aa4a8" },
  cod:       { label: "COD",       bg: "rgba(140,109,70,.16)", fg: "#8C6D46" },
};
const STATUS_KEYS = Object.keys(STATUS);

const REP_COLORS = ["#3E4C59", "#6D3B47", "#922525", "#7A5C8E", "#2F5D50", "#B7791F", "#4A5A6A", "#8C6D46"];
const repColor = (s) => {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return REP_COLORS[h % REP_COLORS.length];
};
const repInitials = (s) => {
  const parts = s.trim().split(/[\s/]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
};

const firstNameOf = (name) => (name || "").trim().split(/\s+/)[0].toLowerCase();

// "Mine" = a rep token matches my first name. Reps come in as "Rudy",
// "Salam / Leighann", "RJ" — split on separators and match loosely.
const repTokens = (rep) => (rep || "").toLowerCase().split(/[/,&]+/).map((t) => t.trim()).filter(Boolean);

const fmtUpdated = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

const hostOf = (url) => {
  try { return new URL(url).host.replace(/^www\./, ""); }
  catch { return (url || "").replace(/^https?:\/\//, "").replace(/^www\./, "").split("/")[0]; }
};

const blankAccount = {
  name: "", rep: "", status: "prospect", website: "", phone: "",
  addresses: [""], emails: [""], numProperties: "", totalUnits: "", notes: "",
  contacts: [],
};

export default function Accounts() {
  const [me, setMe] = useState(() => api.getCachedMe() ?? null);
  const [accounts, setAccounts] = useState(null); // null = loading
  const [users, setUsers] = useState([]); // known profiles → "assigned rep" options
  const [query, setQuery] = useState("");
  const [view, setView] = useState("all"); // all | mine | unassigned
  const [sort, setSort] = useState({ key: "units", dir: "desc" });
  const [editing, setEditing] = useState(null); // account | "new" | null (full edit form)
  const [viewing, setViewing] = useState(null);  // account being viewed (detail + log)
  const [saveState, setSaveState] = useState("idle");

  useEffect(() => {
    if (me) return;
    api.authMe().then(setMe).catch(() => setMe({ signedIn: false, configured: true }));
  }, []);

  const refresh = () => api.listAccounts().then(setAccounts).catch(() => setAccounts([]));
  useEffect(() => {
    if (!me?.signedIn) return;
    refresh();
    api.listUsers().then(setUsers).catch(() => setUsers([]));
  }, [me?.signedIn]);

  // Browser Back closes an open account (detail or form) and returns to the
  // master list instead of leaving the page — same behavior as the CRM board,
  // so the browser Back button and the in-page back button do the same thing.
  const overlayOpen = editing !== null || viewing !== null;
  const closedByBack = useRef(false);
  useEffect(() => {
    if (!overlayOpen) return;
    window.history.pushState({ acctOverlay: true }, "");
    const onPop = () => { closedByBack.current = true; setEditing(null); setViewing(null); };
    window.addEventListener("popstate", onPop);
    return () => {
      window.removeEventListener("popstate", onPop);
      if (!closedByBack.current) window.history.back();
      closedByBack.current = false;
    };
  }, [overlayOpen]);

  const mine = firstNameOf(me?.name);

  const rows = useMemo(() => {
    if (!accounts) return [];
    const q = query.trim().toLowerCase();
    let out = accounts.filter((a) => {
      if (view === "mine") {
        if (!repTokens(a.rep).some((t) => t === mine || t.startsWith(mine))) return false;
      } else if (view === "unassigned") {
        if ((a.rep || "").trim()) return false;
      }
      if (!q) return true;
      const hay = [a.name, a.rep, a.notes, ...(a.emails || []), ...(a.addresses || []),
        ...(a.contacts || []).map((c) => c.name + " " + c.email)].join(" ").toLowerCase();
      return hay.includes(q);
    });
    const dir = sort.dir === "asc" ? 1 : -1;
    out = [...out].sort((a, b) => {
      if (sort.key === "name") return a.name.localeCompare(b.name) * dir;
      if (sort.key === "updated") return ((a.updated || "") < (b.updated || "") ? -1 : 1) * dir;
      // units: nulls always last regardless of direction
      const av = a.totalUnits, bv = b.totalUnits;
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return (av - bv) * dir;
    });
    return out;
  }, [accounts, query, view, sort, mine]);

  const setSortKey = (key) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "desc" ? "asc" : "desc" } : { key, dir: key === "name" ? "asc" : "desc" }));

  const save = async (form) => {
    if (!form.name.trim()) return;
    setSaveState("saving");
    const payload = {
      name: form.name.trim(),
      rep: form.rep.trim(),
      status: form.status,
      website: form.website.trim(),
      phone: form.phone.trim(),
      addresses: form.addresses.map((s) => s.trim()).filter(Boolean),
      emails: form.emails.map((s) => s.trim()).filter(Boolean),
      numProperties: form.numProperties === "" ? null : Number(form.numProperties),
      totalUnits: form.totalUnits === "" ? null : Number(form.totalUnits),
      notes: form.notes,
      contacts: form.contacts
        .filter((c) => c.name.trim())
        .map((c) => ({ name: c.name.trim(), role: c.role.trim(), email: c.email.trim(), phone: c.phone.trim(), address: c.address.trim() })),
    };
    try {
      let saved;
      if (form.id) saved = await api.updateAccount(form.id, payload);
      else saved = await api.createAccount(payload);
      await refresh();
      setSaveState("saved");
      setEditing(null);
      setViewing(saved || null); // land on the account's detail view after saving
      setTimeout(() => setSaveState("idle"), 1200);
    } catch {
      setSaveState("error");
    }
  };

  const remove = async (id) => {
    if (!window.confirm("Delete this account and its contacts? This affects the whole team.")) return;
    setSaveState("saving");
    try {
      await api.deleteAccount(id);
      await refresh();
      setEditing(null);
      setViewing(null);
      setSaveState("idle");
    } catch { setSaveState("error"); }
  };

  // --- Gates ---------------------------------------------------------------
  if (me === null) {
    return <div className="py-16 text-center font-mono text-sm tracking-widest uppercase" style={{ color: SEA }}>Checking sign-in…</div>;
  }
  if (!me.signedIn) {
    return (
      <section className="bg-white rounded-lg p-8 text-center border-l-4 max-w-lg mx-auto mt-8" style={{ borderColor: SEA }}>
        <Building2 size={28} style={{ color: SEA }} className="mx-auto mb-3" />
        <h2 className="text-xl font-bold mb-2" style={{ fontFamily: "Georgia, serif" }}>Star Accounts</h2>
        <p className="text-[15px] mb-5" style={{ color: "#4a5a60" }}>The shared hit list. Sign in with your Star account to view and edit.</p>
        <a href={api.authLoginUrl()} className="inline-flex items-center gap-2 px-5 py-2.5 rounded text-white text-sm font-medium" style={{ background: INK }}>Sign in with Microsoft</a>
      </section>
    );
  }
  if (accounts === null) {
    return <div className="py-16 text-center font-mono text-sm tracking-widest uppercase" style={{ color: SEA }}>Loading accounts…</div>;
  }

  if (editing) {
    return (
      <AccountForm
        initial={editing === "new" ? blankAccount : toForm(editing)}
        users={users}
        onCancel={() => { setEditing(null); setViewing(null); }}
        onSave={save}
        onDelete={remove}
        saveState={saveState}
      />
    );
  }

  if (viewing) {
    return (
      <AccountDetail
        account={viewing}
        onBack={() => setViewing(null)}
        onEdit={() => setEditing(viewing)}
        onDelete={remove}
        onChanged={(a) => { setViewing(a); refresh(); }}
      />
    );
  }

  const Th = ({ label, k, num }) => (
    <th className={"px-3 py-2 font-mono text-[10px] uppercase tracking-wider select-none " + (num ? "text-right" : "text-left")} style={{ color: "#6b7a80" }}>
      <button onClick={() => setSortKey(k)} className="inline-flex items-center gap-1 hover:text-black" style={{ color: sort.key === k ? SEA : "inherit" }}>
        {label}{sort.key === k ? <ArrowUpDown size={11} /> : null}
      </button>
    </th>
  );

  return (
    <div>
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div className="relative flex-1 min-w-56">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "#8b9a9f" }} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search company, rep, contact, email, notes"
            className="w-full bg-white border rounded pl-9 pr-3 py-2 text-sm" style={{ borderColor: BORDER }} />
        </div>
        <div className="flex shrink-0 gap-0.5 p-0.5 rounded bg-white" style={{ border: "1px solid " + BORDER }}>
          {[["all", "All"], ["mine", "My accounts"], ["unassigned", "Unassigned"]].map(([k, label]) => (
            <button key={k} onClick={() => setView(k)} className="px-3 py-1.5 text-sm font-medium rounded whitespace-nowrap"
              style={view === k ? { background: INK, color: "white" } : { background: "white", color: INK }}>{label}</button>
          ))}
        </div>
        <span className="font-mono text-xs" style={{ color: saveState === "error" ? TIDE : SEA }}>
          {saveState === "saving" ? "saving…" : saveState === "saved" ? "saved ✓" : saveState === "error" ? "save failed" : ""}
        </span>
        <button onClick={() => setEditing("new")} className="flex items-center gap-1.5 px-3 py-2 rounded text-white text-sm font-medium shrink-0" style={{ background: INK }}>
          <Plus size={16} /> New account
        </button>
      </div>

      <div className="text-xs mb-2" style={{ color: "#8b9a9f" }}>
        {rows.length} account{rows.length === 1 ? "" : "s"}{view === "mine" ? " assigned to you" : view === "unassigned" ? " with no rep" : ""}
        {" · "}{sort.key === "units" ? "ranked by units" : sort.key === "name" ? "sorted by name" : "sorted by last updated"}{sort.dir === "asc" ? " (asc)" : ""}
      </div>

      {/* Table */}
      <div className="bg-white rounded-lg border overflow-x-auto" style={{ borderColor: BORDER }}>
        <table className="w-full text-sm" style={{ minWidth: 820 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid " + BORDER }}>
              <Th label="Account" k="name" />
              <th className="px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-left" style={{ color: "#6b7a80" }}>Rep</th>
              <th className="px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-left" style={{ color: "#6b7a80" }}>Contact</th>
              <th className="px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-left" style={{ color: "#6b7a80" }}>Phone</th>
              <th className="px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-left" style={{ color: "#6b7a80" }}>Website</th>
              <Th label="Units" k="units" num />
              <th className="px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-left" style={{ color: "#6b7a80" }}>Status</th>
              <Th label="Updated" k="updated" />
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => {
              const st = STATUS[a.status] || STATUS.prospect;
              const primary = a.contacts?.[0];
              const extra = (a.contacts?.length || 0) - 1;
              return (
                <tr key={a.id} onClick={() => setViewing(a)} className="cursor-pointer hover:bg-stone-50" style={{ borderBottom: "1px solid " + ROW_LINE }}>
                  <td className="px-3 py-2.5 align-top">
                    <div className="font-semibold break-words" style={{ color: INK, maxWidth: "18rem" }}>{a.name}</div>
                    {a.addresses?.[0] && <div className="text-xs truncate" style={{ color: "#8b9a9f", maxWidth: "18rem" }}>{a.addresses[0]}</div>}
                  </td>
                  <td className="px-3 py-2.5">
                    {a.rep ? (
                      <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
                        <span className="w-5 h-5 rounded-full text-white text-[9px] font-bold flex items-center justify-center" style={{ background: repColor(a.rep) }}>{repInitials(a.rep)}</span>
                        <span className="text-[13px]">{a.rep}</span>
                      </span>
                    ) : <span className="text-xs" style={{ color: "#b0b8ba" }}>unassigned</span>}
                  </td>
                  <td className="px-3 py-2.5">
                    {primary ? (
                      <span className="inline-flex items-center gap-1.5">
                        <span className="text-[13px]">{primary.name}</span>
                        {primary.email && <Mail size={12} style={{ color: SEA }} />}
                        {extra > 0 && <span className="font-mono text-[10px] font-bold px-1.5 rounded-full" style={{ background: "rgba(146,37,37,.10)", color: SEA }}>+{extra}</span>}
                      </span>
                    ) : <span className="text-xs" style={{ color: "#b0b8ba" }}>—</span>}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-[12px] whitespace-nowrap" style={{ color: "#4a5a60" }}>{a.phone || "—"}</td>
                  <td className="px-3 py-2.5">
                    {a.website ? (
                      <a href={a.website} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className="inline-flex items-center gap-1 text-[12px] underline" style={{ color: SEA }}>
                        <Globe size={12} /> {hostOf(a.website)}
                      </a>
                    ) : <span className="text-xs" style={{ color: "#b0b8ba" }}>—</span>}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-[13px] font-semibold tabular-nums" style={{ color: INK }}>
                    {a.totalUnits != null ? a.totalUnits.toLocaleString() : <span style={{ color: "#b0b8ba" }}>—</span>}
                  </td>
                  <td className="px-3 py-2.5">
                    <span className="font-mono text-[10px] font-bold px-2 py-0.5 rounded-full whitespace-nowrap" style={{ background: st.bg, color: st.fg }}>{st.label}</span>
                  </td>
                  <td className="px-3 py-2.5 whitespace-nowrap">
                    {a.updated ? (
                      <div>
                        <div className="font-mono text-[12px] tabular-nums" style={{ color: "#6b7a80" }}>{fmtUpdated(a.updated)}</div>
                        {a.updatedBy && a.updatedBy !== "seed" && <div className="text-[10px]" style={{ color: "#b0b8ba" }}>{a.updatedBy}</div>}
                      </div>
                    ) : <span className="text-xs" style={{ color: "#b0b8ba" }}>—</span>}
                  </td>
                </tr>
              );
            })}
            {rows.length === 0 && (
              <tr><td colSpan={8} className="text-sm text-center py-10" style={{ color: "#8b9a9f" }}>No accounts match. Clear the search or add one.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Map a serialized account to the editable form shape (arrays get a blank
// starter row so there's always an input to type into).
function toForm(a) {
  return {
    id: a.id, name: a.name, rep: a.rep || "", status: a.status || "prospect",
    website: a.website || "", phone: a.phone || "",
    addresses: (a.addresses && a.addresses.length ? a.addresses : [""]).slice(),
    emails: (a.emails && a.emails.length ? a.emails : [""]).slice(),
    numProperties: a.numProperties == null ? "" : String(a.numProperties),
    totalUnits: a.totalUnits == null ? "" : String(a.totalUnits),
    notes: a.notes || "",
    contacts: (a.contacts || []).map((c) => ({ ...c })),
  };
}

function AccountForm({ initial, users, onCancel, onSave, onDelete, saveState }) {
  const [form, setForm] = useState(initial);
  const [attachOpen, setAttachOpen] = useState(false);
  const [personal, setPersonal] = useState(null); // personal contacts for attach
  const [attachQ, setAttachQ] = useState("");
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  // "Assigned rep" suggestions come from the known user profiles, offered via a
  // datalist so the field stays free-text: type-ahead autocompletes a real name,
  // but you can also type a custom value or two reps ("Salam / Leighann").
  const repNames = [...new Set((users || []).map((u) => u.name).filter(Boolean))];

  const field = "w-full bg-white border rounded px-3 py-2 text-sm";
  const bc = { borderColor: BORDER };
  const cap = "font-mono text-[10px] uppercase tracking-widest block mb-1";

  // Repeatable string lists (addresses, emails)
  const setListItem = (key, i) => (e) => {
    const next = form[key].slice(); next[i] = e.target.value; setForm({ ...form, [key]: next });
  };
  const addListItem = (key) => setForm({ ...form, [key]: [...form[key], ""] });
  const removeListItem = (key, i) => setForm({ ...form, [key]: form[key].filter((_, j) => j !== i) });

  // Contacts sub-list
  const setContact = (i, k) => (e) => {
    const next = form.contacts.slice(); next[i] = { ...next[i], [k]: e.target.value }; setForm({ ...form, contacts: next });
  };
  const addContact = () => setForm({ ...form, contacts: [...form.contacts, { name: "", role: "", email: "", phone: "", address: "" }] });
  const removeContact = (i) => setForm({ ...form, contacts: form.contacts.filter((_, j) => j !== i) });

  const openAttach = () => {
    setAttachOpen(true);
    if (personal === null) api.listContacts().then(setPersonal).catch(() => setPersonal([]));
  };
  const attach = (c) => {
    setForm({ ...form, contacts: [...form.contacts, { name: c.name, role: c.role || c.company || "", email: c.email || "", phone: c.phone || "", address: "" }] });
    setAttachOpen(false); setAttachQ("");
  };
  const attachRows = (personal || []).filter((c) => {
    const q = attachQ.trim().toLowerCase();
    return !q || [c.name, c.company, c.email].join(" ").toLowerCase().includes(q);
  }).slice(0, 8);

  // Plain render function (NOT a nested component) so the inputs keep focus
  // while typing and `listKey` isn't swallowed as a React `key` prop.
  const renderList = (label, listKey, placeholder, type) => (
    <div>
      <span className={cap} style={{ color: SEA }}>{label}</span>
      <div className="space-y-2">
        {form[listKey].map((v, i) => (
          <div key={i} className="flex gap-2">
            <input className={field} style={bc} type={type || "text"} placeholder={placeholder} value={v} maxLength={300} autoComplete="off" onChange={setListItem(listKey, i)} />
            <button onClick={() => removeListItem(listKey, i)} className="px-2 rounded hover:bg-stone-100 shrink-0" style={{ color: TIDE }} title="Remove"><X size={15} /></button>
          </div>
        ))}
        <button onClick={() => addListItem(listKey)} className="text-xs font-medium flex items-center gap-1" style={{ color: SEA }}><Plus size={13} /> Add {label.toLowerCase()}</button>
      </div>
    </div>
  );

  return (
    <section className="bg-white rounded-lg p-5 border-l-4 max-w-3xl" style={{ borderColor: SEA }}>
      <div className="flex items-center justify-between mb-4">
        <button onClick={onCancel} className="flex items-center gap-1 font-mono text-xs uppercase tracking-widest" style={{ color: SEA }}>
          <ChevronLeft size={14} /> All accounts
        </button>
        <div className="flex items-center gap-2">
          {form.id && <button onClick={() => onDelete(form.id)} className="p-2 rounded hover:bg-stone-100" style={{ color: TIDE }} title="Delete account"><Trash2 size={16} /></button>}
        </div>
      </div>
      <h2 className="text-2xl font-bold mb-4" style={{ fontFamily: "Georgia, serif" }}>{form.id ? "Edit account" : "New account"}</h2>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block sm:col-span-2">
          <span className={cap} style={{ color: SEA }}>Company / property name *</span>
          <input className={field} style={bc} placeholder="e.g. CONAM Management Corporation" value={form.name} maxLength={200} autoComplete="off" onChange={set("name")} />
        </label>
        <label className="block">
          <span className={cap} style={{ color: SEA }}>Assigned rep</span>
          <input className={field} style={bc} list="account-reps" maxLength={120}
            placeholder="Type or pick — use / for two (Salam / Leighann)"
            value={form.rep} onChange={set("rep")} />
          <datalist id="account-reps">
            {repNames.map((r) => <option key={r} value={r} />)}
          </datalist>
        </label>
        <label className="block">
          <span className={cap} style={{ color: SEA }}>Status</span>
          <select className={field} style={bc} value={form.status} onChange={set("status")}>
            {STATUS_KEYS.map((k) => <option key={k} value={k}>{STATUS[k].label}</option>)}
          </select>
        </label>
        <label className="block">
          <span className={cap} style={{ color: SEA }}>Phone</span>
          <input className={field} style={bc} placeholder="(619) 555-0100" value={form.phone} maxLength={40} autoComplete="off" onChange={set("phone")} />
        </label>
        <label className="block">
          <span className={cap} style={{ color: SEA }}>Website</span>
          <input className={field} style={bc} placeholder="https://…" value={form.website} maxLength={300} autoComplete="off" onChange={set("website")} />
        </label>
        <label className="block">
          <span className={cap} style={{ color: SEA }}># Properties</span>
          <input className={field} style={bc} type="number" min="0" placeholder="e.g. 91" value={form.numProperties} autoComplete="off" onChange={set("numProperties")} />
        </label>
        <label className="block">
          <span className={cap} style={{ color: SEA }}>Total units</span>
          <input className={field} style={bc} type="number" min="0" placeholder="e.g. 10744" value={form.totalUnits} autoComplete="off" onChange={set("totalUnits")} />
        </label>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 mt-3">
        {renderList("Addresses", "addresses", "Street, City, ZIP")}
        {renderList("Emails", "emails", "name@company.com", "email")}
      </div>

      <label className="block mt-3">
        <span className={cap} style={{ color: SEA }}>Notes</span>
        <textarea className={field + " h-24"} style={bc} placeholder="Visit history, context, who uses whom…" value={form.notes} maxLength={5000} onChange={set("notes")} />
      </label>

      {/* Contacts */}
      <div className="mt-5">
        <div className="flex items-center justify-between mb-2">
          <span className="font-mono text-xs uppercase tracking-widest" style={{ color: INK }}>Contacts ({form.contacts.length})</span>
          <div className="flex items-center gap-2">
            <button onClick={openAttach} className="text-xs font-medium flex items-center gap-1 px-2 py-1 rounded" style={{ color: SEA, border: "1px dashed " + BORDER }}>
              <UserPlus size={13} /> Attach a personal contact
            </button>
            <button onClick={addContact} className="text-xs font-medium flex items-center gap-1 px-2 py-1 rounded text-white" style={{ background: INK }}>
              <Plus size={13} /> Add contact
            </button>
          </div>
        </div>

        {attachOpen && (
          <div className="mb-3 rounded border p-3" style={{ borderColor: BORDER, background: MIST }}>
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium" style={{ color: "#4a5a60" }}>Pick one of your personal CRM contacts — it becomes visible to the whole team on this account.</span>
              <button onClick={() => setAttachOpen(false)} className="p-1 rounded hover:bg-white" style={{ color: INK }}><X size={14} /></button>
            </div>
            <input autoFocus value={attachQ} onChange={(e) => setAttachQ(e.target.value)} placeholder="Search your contacts…" className="w-full bg-white border rounded px-3 py-1.5 text-sm mb-2" style={bc} />
            {personal === null ? (
              <div className="text-xs py-2" style={{ color: "#8b9a9f" }}>Loading your contacts…</div>
            ) : attachRows.length === 0 ? (
              <div className="text-xs py-2" style={{ color: "#8b9a9f" }}>No matches.</div>
            ) : (
              <ul className="space-y-1">
                {attachRows.map((c) => (
                  <li key={c.id}>
                    <button onClick={() => attach(c)} className="w-full text-left bg-white rounded px-3 py-1.5 hover:shadow-sm flex items-center justify-between" style={{ border: "1px solid " + BORDER }}>
                      <span className="text-sm"><b>{c.name}</b>{c.company ? <span style={{ color: "#8b9a9f" }}> · {c.company}</span> : null}</span>
                      <span className="font-mono text-[11px]" style={{ color: "#8b9a9f" }}>{c.email}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {form.contacts.length === 0 && !attachOpen && (
          <div className="text-sm rounded p-3" style={{ background: MIST, color: "#8b9a9f" }}>No contacts yet. Add one, or attach a personal contact.</div>
        )}

        <div className="space-y-3">
          {form.contacts.map((c, i) => (
            <div key={i} className="rounded border p-3" style={{ borderColor: BORDER }}>
              <div className="flex items-center justify-between mb-2">
                <span className="font-mono text-[10px] uppercase tracking-widest" style={{ color: SEA }}>Contact {i + 1}</span>
                <button onClick={() => removeContact(i)} className="p-1 rounded hover:bg-stone-100" style={{ color: TIDE }} title="Remove contact"><Trash2 size={14} /></button>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                <input className={field} style={bc} placeholder="Name" value={c.name} maxLength={200} autoComplete="off" onChange={setContact(i, "name")} />
                <input className={field} style={bc} placeholder="Role (e.g. AP / Billing)" value={c.role} maxLength={120} autoComplete="off" onChange={setContact(i, "role")} />
                <input className={field} style={bc} type="email" placeholder="Email" value={c.email} maxLength={254} autoComplete="off" onChange={setContact(i, "email")} />
                <input className={field} style={bc} placeholder="Phone" value={c.phone} maxLength={40} autoComplete="off" onChange={setContact(i, "phone")} />
                <input className={field + " sm:col-span-2"} style={bc} placeholder="Address (optional)" value={c.address} maxLength={300} autoComplete="off" onChange={setContact(i, "address")} />
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-2 mt-5">
        <button onClick={() => onSave(form)} disabled={!form.name.trim() || saveState === "saving"} className="px-4 py-2 rounded text-white text-sm font-medium disabled:opacity-50" style={{ background: SEA }}>
          {saveState === "saving" ? "Saving…" : form.id ? "Save changes" : "Create account"}
        </button>
        <button onClick={onCancel} className="px-4 py-2 rounded text-sm" style={{ background: MIST }}>Cancel</button>
        {saveState === "error" && <span className="text-xs" style={{ color: TIDE }}>Save failed — try again.</span>}
      </div>
    </section>
  );
}

// Rebuild the full update payload from a serialized account plus overrides.
// Quick-edits on the detail view PUT the whole account (the endpoint replaces
// contacts/emails wholesale), so we reconstruct the payload from current state.
function accountToPayload(a, overrides = {}) {
  const base = {
    name: a.name,
    rep: a.rep || "",
    status: a.status || "prospect",
    website: a.website || "",
    phone: a.phone || "",
    addresses: a.addresses || [],
    emails: a.emails || [],
    numProperties: a.numProperties ?? null,
    totalUnits: a.totalUnits ?? null,
    notes: a.notes || "",
    contacts: (a.contacts || []).map((c) => ({
      name: c.name, role: c.role || "", email: c.email || "", phone: c.phone || "", address: c.address || "",
    })),
  };
  return { ...base, ...overrides };
}

// Read-first detail view: the account, its contacts, its notes, and a dated
// activity log with a "Log note" box — mirroring the CRM's touch log. Small
// quick-edits (log a note, add an email/contact, edit notes) live here so you
// don't drop into the full form for a routine update; "Edit" opens the form.
function AccountDetail({ account, onBack, onEdit, onDelete, onChanged }) {
  const [acct, setAcct] = useState(account);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [editNotes, setEditNotes] = useState(false);
  const [notesDraft, setNotesDraft] = useState(account.notes || "");
  const [addEmail, setAddEmail] = useState(false);
  const [emailDraft, setEmailDraft] = useState("");
  const [addContact, setAddContact] = useState(false);
  const [cDraft, setCDraft] = useState({ name: "", role: "", email: "", phone: "", address: "" });

  useEffect(() => { setAcct(account); setNotesDraft(account.notes || ""); }, [account]);

  const st = STATUS[acct.status] || STATUS.prospect;
  const field = "w-full bg-white border rounded px-3 py-2 text-sm";
  const bc = { borderColor: BORDER };
  const cap = "font-mono text-[10px] uppercase tracking-widest block mb-1";

  const put = async (overrides) => {
    setBusy(true);
    try {
      const updated = await api.updateAccount(acct.id, accountToPayload(acct, overrides));
      setAcct(updated); onChanged(updated);
      return true;
    } catch { return false; }
    finally { setBusy(false); }
  };

  const logNote = async () => {
    const n = note.trim();
    if (!n || busy) return;
    setBusy(true);
    try {
      const updated = await api.logAccountNote(acct.id, n);
      setAcct(updated); onChanged(updated); setNote("");
    } catch { /* ignore */ }
    finally { setBusy(false); }
  };
  const saveNotes = async () => { if (await put({ notes: notesDraft })) setEditNotes(false); };
  const saveEmail = async () => {
    const e = emailDraft.trim();
    if (!e) { setAddEmail(false); return; }
    if (await put({ emails: [...(acct.emails || []), e] })) { setAddEmail(false); setEmailDraft(""); }
  };
  const saveContact = async () => {
    if (!cDraft.name.trim()) { setAddContact(false); return; }
    if (await put({ contacts: [...(acct.contacts || []), cDraft] })) {
      setAddContact(false); setCDraft({ name: "", role: "", email: "", phone: "", address: "" });
    }
  };

  return (
    <section className="bg-white rounded-lg p-5 border-l-4 max-w-3xl" style={{ borderColor: SEA }}>
      <div className="flex items-center justify-between mb-3">
        <button onClick={onBack} className="flex items-center gap-1 font-mono text-xs uppercase tracking-widest" style={{ color: SEA }}>
          <ChevronLeft size={14} /> All accounts
        </button>
        <div className="flex items-center gap-2">
          <button onClick={onEdit} className="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium" style={{ background: "white", color: INK, border: "1px solid " + BORDER }}>
            <Pencil size={14} /> Edit
          </button>
          <button onClick={() => onDelete(acct.id)} className="p-2 rounded hover:bg-stone-100" style={{ color: TIDE }} title="Delete account"><Trash2 size={16} /></button>
        </div>
      </div>

      <div className="flex items-start justify-between gap-3 flex-wrap mb-1">
        <h2 className="text-2xl font-bold break-words" style={{ fontFamily: "Georgia, serif", maxWidth: "34rem" }}>{acct.name}</h2>
        <span className="font-mono text-[10px] font-bold px-2 py-0.5 rounded-full" style={{ background: st.bg, color: st.fg }}>{st.label}</span>
      </div>
      <div className="flex items-center gap-3 flex-wrap text-sm mb-4" style={{ color: "#4a5a60" }}>
        {acct.rep
          ? <span className="inline-flex items-center gap-1.5"><span className="w-5 h-5 rounded-full text-white text-[9px] font-bold flex items-center justify-center" style={{ background: repColor(acct.rep) }}>{repInitials(acct.rep)}</span>{acct.rep}</span>
          : <span style={{ color: "#b0b8ba" }}>Unassigned</span>}
        {acct.phone && <span className="inline-flex items-center gap-1"><Phone size={13} style={{ color: SEA }} />{acct.phone}</span>}
        {acct.website && <a href={acct.website} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 underline" style={{ color: SEA }}><Globe size={13} />{hostOf(acct.website)}</a>}
        {acct.totalUnits != null && <span className="font-mono text-xs">{acct.totalUnits.toLocaleString()} units{acct.numProperties != null ? ` · ${acct.numProperties} props` : ""}</span>}
      </div>

      {(acct.addresses || []).length > 0 && (
        <div className="mb-3">
          <div className={cap} style={{ color: SEA }}>Addresses</div>
          <ul className="text-sm space-y-0.5">{acct.addresses.map((ad, i) => <li key={i} style={{ color: "#4a5a60" }}>{ad}</li>)}</ul>
        </div>
      )}

      <div className="mb-3">
        <div className="flex items-center justify-between">
          <div className={cap} style={{ color: SEA }}>Emails</div>
          <button onClick={() => setAddEmail((v) => !v)} className="text-xs font-medium flex items-center gap-1" style={{ color: SEA }}><Plus size={12} /> Add email</button>
        </div>
        {(acct.emails || []).length > 0
          ? <ul className="text-sm space-y-0.5">{acct.emails.map((em, i) => <li key={i}><a href={"mailto:" + em} className="underline" style={{ color: SEA }}>{em}</a></li>)}</ul>
          : <div className="text-sm" style={{ color: "#b0b8ba" }}>None yet.</div>}
        {addEmail && (
          <div className="flex gap-2 mt-2">
            <input autoFocus className={field} style={bc} type="email" maxLength={254} autoComplete="off" placeholder="name@company.com" value={emailDraft} onChange={(e) => setEmailDraft(e.target.value)} onKeyDown={(e) => e.key === "Enter" && saveEmail()} />
            <button onClick={saveEmail} disabled={busy} className="px-3 rounded text-white text-sm shrink-0" style={{ background: SEA }}>Add</button>
          </div>
        )}
      </div>

      <div className="mb-4">
        <div className="flex items-center justify-between mb-1">
          <div className={cap} style={{ color: SEA }}>Contacts ({(acct.contacts || []).length})</div>
          <button onClick={() => setAddContact((v) => !v)} className="text-xs font-medium flex items-center gap-1" style={{ color: SEA }}><UserPlus size={12} /> Add contact</button>
        </div>
        <div className="space-y-1.5">
          {(acct.contacts || []).map((c) => (
            <div key={c.id || c.name} className="rounded border p-2.5" style={{ borderColor: BORDER }}>
              <div className="text-sm font-semibold">{c.name}{c.role ? <span className="font-normal" style={{ color: "#8b9a9f" }}> · {c.role}</span> : null}</div>
              <div className="flex gap-3 flex-wrap mt-0.5 text-[13px]">
                {c.email && <a href={"mailto:" + c.email} className="underline inline-flex items-center gap-1" style={{ color: SEA }}><Mail size={12} />{c.email}</a>}
                {c.phone && <span className="inline-flex items-center gap-1" style={{ color: "#4a5a60" }}><Phone size={12} />{c.phone}</span>}
              </div>
              {c.address && <div className="text-xs mt-0.5" style={{ color: "#8b9a9f" }}>{c.address}</div>}
            </div>
          ))}
          {(acct.contacts || []).length === 0 && !addContact && <div className="text-sm" style={{ color: "#b0b8ba" }}>No contacts yet.</div>}
        </div>
        {addContact && (
          <div className="rounded border p-3 mt-2" style={{ borderColor: BORDER, background: MIST }}>
            <div className="grid gap-2 sm:grid-cols-2">
              <input autoFocus className={field} style={bc} maxLength={200} autoComplete="off" placeholder="Name" value={cDraft.name} onChange={(e) => setCDraft({ ...cDraft, name: e.target.value })} />
              <input className={field} style={bc} maxLength={120} autoComplete="off" placeholder="Role" value={cDraft.role} onChange={(e) => setCDraft({ ...cDraft, role: e.target.value })} />
              <input className={field} style={bc} type="email" maxLength={254} autoComplete="off" placeholder="Email" value={cDraft.email} onChange={(e) => setCDraft({ ...cDraft, email: e.target.value })} />
              <input className={field} style={bc} maxLength={40} autoComplete="off" placeholder="Phone" value={cDraft.phone} onChange={(e) => setCDraft({ ...cDraft, phone: e.target.value })} />
            </div>
            <div className="flex gap-2 mt-2">
              <button onClick={saveContact} disabled={busy} className="px-3 py-1.5 rounded text-white text-sm" style={{ background: SEA }}>Add contact</button>
              <button onClick={() => setAddContact(false)} className="px-3 py-1.5 rounded text-sm" style={{ background: MIST }}>Cancel</button>
            </div>
          </div>
        )}
      </div>

      <div className="mb-5">
        <div className="flex items-center justify-between mb-1">
          <div className={cap} style={{ color: SEA }}>Notes</div>
          {!editNotes && <button onClick={() => { setNotesDraft(acct.notes || ""); setEditNotes(true); }} className="text-xs font-medium flex items-center gap-1" style={{ color: SEA }}><Pencil size={12} /> Edit</button>}
        </div>
        {editNotes ? (
          <div>
            <textarea className={field + " h-24"} style={bc} maxLength={5000} value={notesDraft} onChange={(e) => setNotesDraft(e.target.value)} />
            <div className="flex gap-2 mt-2">
              <button onClick={saveNotes} disabled={busy} className="px-3 py-1.5 rounded text-white text-sm" style={{ background: SEA }}>Save notes</button>
              <button onClick={() => setEditNotes(false)} className="px-3 py-1.5 rounded text-sm" style={{ background: MIST }}>Cancel</button>
            </div>
          </div>
        ) : (
          acct.notes ? <p className="text-[15px] leading-relaxed whitespace-pre-wrap" style={{ color: INK }}>{acct.notes}</p> : <div className="text-sm" style={{ color: "#b0b8ba" }}>No notes yet.</div>
        )}
      </div>

      <div>
        <div className="font-mono text-xs uppercase tracking-widest mb-2" style={{ color: INK }}>Activity</div>
        <div className="flex gap-2">
          <input value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={(e) => e.key === "Enter" && logNote()} placeholder="Log a note — visited, called, dropped samples…" maxLength={2000} autoComplete="off" className="flex-1 border rounded px-3 py-2 text-sm bg-white" style={bc} />
          <button onClick={logNote} disabled={busy || !note.trim()} className="px-3 py-2 rounded text-white text-sm font-medium disabled:opacity-50 shrink-0" style={{ background: INK }}>Log note</button>
        </div>
        <ul className="mt-3 space-y-2">
          {(acct.log || []).map((l, i) => (
            <li key={i} className="flex gap-3 text-sm">
              <span className="font-mono text-xs pt-0.5 shrink-0" style={{ color: SEA }}>{l.date}</span>
              <span>{l.note}{l.by ? <span className="text-xs ml-1" style={{ color: "#b0b8ba" }}>— {l.by}</span> : null}</span>
            </li>
          ))}
          {(acct.log || []).length === 0 && <li className="text-sm" style={{ color: "#8b9a9f" }}>No activity yet. Log the first note above.</li>}
        </ul>
      </div>
    </section>
  );
}
