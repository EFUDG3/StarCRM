// Projects tab: the PM team's board of large commercial jobs (schools,
// restaurants, retail build-outs). Shared company-wide like Accounts — every
// signed-in user sees and edits every project.
//
// Deliberately lean. The job here is that a project manager can open this tab
// before a weekly meeting and know exactly where every job stands: grouped by
// stage with "In progress" as the main lane, target dates that shout when
// they're late, and a dated job log that IS the status history.
//
// NOT a Procore clone: no schedules, no submittals, no document storage, and
// money stops at a reference contract value (QuickBooks is the book of record).
// Separate from the Accounts hit list, which is the sales team's tool.
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Plus, Search, Trash2, ChevronLeft, ChevronDown, ChevronRight, Pencil,
  MapPin, Calendar, HardHat, Ruler, AlertTriangle,
} from "lucide-react";
import * as api from "./api.js";

const INK = "#1C1C1C";
const MIST = "#F3F0EC";
const SEA = "#922525";
const TIDE = "#C0392B";
const BORDER = "#cdd6d4";
const ROW_LINE = "#e4dfd3"; // shared warm separator (CRM + Accounts tables)

// Stage = the board's grouping. Display order is what a PM cares about at a
// weekly meeting: what's running now, what's wrapping up, what's about to
// start, what we're chasing, then the closed stuff. `open` = expanded by
// default (collapse state is remembered per user in localStorage).
const STAGES = [
  { key: "in_progress", label: "In progress", bg: "rgba(47,93,80,.16)",   fg: "#2b6a58", open: true, main: true },
  { key: "punch_list",  label: "Punch list",  bg: "rgba(140,109,70,.16)", fg: "#8C6D46", open: true },
  { key: "awarded",     label: "Awarded",     bg: "rgba(62,76,89,.14)",   fg: "#3E4C59", open: true },
  { key: "bidding",     label: "Bidding",     bg: "#ece7df",              fg: "#6b7a80", open: false },
  { key: "complete",    label: "Complete",    bg: "#2F5D50",              fg: "#ffffff", open: false },
  { key: "lost",        label: "Lost",        bg: "#e7e2da",              fg: "#9aa4a8", open: false },
];
const STAGE_BY_KEY = Object.fromEntries(STAGES.map((s) => [s.key, s]));
const STAGE_KEYS = STAGES.map((s) => s.key);

const TYPES = {
  school: "School",
  restaurant: "Restaurant",
  retail: "Retail",
  multifamily: "Multifamily",
  office: "Office",
  other: "Other",
};
const TYPE_KEYS = Object.keys(TYPES);

const PM_COLORS = ["#3E4C59", "#6D3B47", "#922525", "#7A5C8E", "#2F5D50", "#B7791F", "#4A5A6A", "#8C6D46"];
const pmColor = (s) => {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return PM_COLORS[h % PM_COLORS.length];
};
const pmInitials = (s) => {
  const parts = s.trim().split(/[\s/]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
};

const firstNameOf = (name) => (name || "").trim().split(/\s+/)[0].toLowerCase();
// PMs come in as "Marc", "Marc / RJ" — split on separators and match loosely,
// same convention as the Accounts rep field.
const pmTokens = (pm) => (pm || "").toLowerCase().split(/[/,&]+/).map((t) => t.trim()).filter(Boolean);

const fmtMoney = (v) => (v == null ? "" : "$" + Math.round(v).toLocaleString());

const fmtDate = (iso) => {
  if (!iso) return "";
  // Parse as local, not UTC: new Date("2026-08-20") is midnight UTC, which
  // renders as the 19th in Pacific. Build from parts instead.
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const d = new Date(+m[1], +m[2] - 1, +m[3]);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "2-digit" });
};

// Days until a target date (negative = overdue). Null when there's no date.
const daysUntil = (iso) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
  if (!m) return null;
  const target = new Date(+m[1], +m[2] - 1, +m[3]);
  const now = new Date();
  const t0 = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((target - t0) / 86400000);
};

// Urgency only matters while a job is live — a completed job past its target
// date isn't late, it's done.
const LIVE_STAGES = new Set(["in_progress", "punch_list", "awarded", "bidding"]);
const urgencyOf = (p) => {
  if (!LIVE_STAGES.has(p.stage)) return null;
  const d = daysUntil(p.targetDate);
  if (d === null) return null;
  if (d < 0) return { kind: "overdue", label: `${Math.abs(d)}d late`, color: TIDE };
  if (d === 0) return { kind: "today", label: "due today", color: TIDE };
  if (d <= 7) return { kind: "soon", label: `${d}d left`, color: "#B7791F" };
  return null;
};

const COLLAPSE_KEY = "starProjectsCollapsed";

const blankProject = {
  name: "", client: "", siteAddress: "", projectType: "school", stage: "in_progress",
  pm: "", contractValue: "", startDate: "", targetDate: "", material: "", sqFt: "",
  description: "",
};

export default function Projects() {
  const [me, setMe] = useState(() => api.getCachedMe() ?? null);
  const [projects, setProjects] = useState(null); // null = loading
  const [users, setUsers] = useState([]);         // known profiles → PM options
  const [query, setQuery] = useState("");
  const [view, setView] = useState("all");        // all | mine | unassigned
  const [editing, setEditing] = useState(null);   // project | "new" | null
  const [viewing, setViewing] = useState(null);   // project being viewed
  const [saveState, setSaveState] = useState("idle");
  const [collapsed, setCollapsed] = useState(() => {
    try {
      const raw = JSON.parse(localStorage.getItem(COLLAPSE_KEY) || "null");
      if (Array.isArray(raw)) return new Set(raw);
    } catch { /* fall through to defaults */ }
    return new Set(STAGES.filter((s) => !s.open).map((s) => s.key));
  });

  useEffect(() => {
    if (me) return;
    api.authMe().then(setMe).catch(() => setMe({ signedIn: false, configured: true }));
  }, []);

  const refresh = () => api.listProjects().then(setProjects).catch(() => setProjects([]));
  useEffect(() => {
    if (!me?.signedIn) return;
    refresh();
    api.listUsers().then(setUsers).catch(() => setUsers([]));
  }, [me?.signedIn]);

  const toggleStage = (key) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...next])); } catch { /* ignore */ }
      return next;
    });
  };

  // Browser Back closes an open project (detail or form) and returns to the
  // board instead of leaving the page — matches the CRM and Accounts tabs.
  const overlayOpen = editing !== null || viewing !== null;
  const closedByBack = useRef(false);
  useEffect(() => {
    if (!overlayOpen) return;
    window.history.pushState({ projOverlay: true }, "");
    const onPop = () => { closedByBack.current = true; setEditing(null); setViewing(null); };
    window.addEventListener("popstate", onPop);
    return () => {
      window.removeEventListener("popstate", onPop);
      if (!closedByBack.current) window.history.back();
      closedByBack.current = false;
    };
  }, [overlayOpen]);

  const mine = firstNameOf(me?.name);

  const filtered = useMemo(() => {
    if (!projects) return [];
    const q = query.trim().toLowerCase();
    return projects.filter((p) => {
      if (view === "mine") {
        if (!pmTokens(p.pm).some((t) => t === mine || t.startsWith(mine))) return false;
      } else if (view === "unassigned") {
        if ((p.pm || "").trim()) return false;
      }
      if (!q) return true;
      const hay = [p.name, p.client, p.pm, p.siteAddress, p.material, p.description,
        TYPES[p.projectType] || ""].join(" ").toLowerCase();
      return hay.includes(q);
    });
  }, [projects, query, view, mine]);

  // Group by stage. Within a group: live jobs by soonest target date (undated
  // last), closed jobs by most recently touched.
  const grouped = useMemo(() => {
    const out = {};
    for (const k of STAGE_KEYS) out[k] = [];
    for (const p of filtered) (out[p.stage] || out.in_progress).push(p);
    for (const k of STAGE_KEYS) {
      out[k].sort((a, b) => {
        if (LIVE_STAGES.has(k)) {
          const ad = a.targetDate || "", bd = b.targetDate || "";
          if (ad && bd) return ad < bd ? -1 : ad > bd ? 1 : 0;
          if (ad) return -1;
          if (bd) return 1;
        }
        return (a.updated || "") > (b.updated || "") ? -1 : 1;
      });
    }
    return out;
  }, [filtered]);

  const liveCount = STAGE_KEYS.filter((k) => LIVE_STAGES.has(k))
    .reduce((n, k) => n + grouped[k].length, 0);
  const lateCount = filtered.filter((p) => urgencyOf(p)?.kind === "overdue").length;

  const save = async (form) => {
    if (!form.name.trim()) return;
    setSaveState("saving");
    const payload = {
      name: form.name.trim(),
      client: form.client.trim(),
      siteAddress: form.siteAddress.trim(),
      projectType: form.projectType,
      stage: form.stage,
      pm: form.pm.trim(),
      contractValue: form.contractValue === "" ? null : Number(form.contractValue),
      startDate: form.startDate,
      targetDate: form.targetDate,
      material: form.material.trim(),
      sqFt: form.sqFt === "" ? null : Number(form.sqFt),
      description: form.description,
    };
    try {
      const saved = form.id
        ? await api.updateProject(form.id, payload)
        : await api.createProject(payload);
      await refresh();
      setSaveState("saved");
      setEditing(null);
      setViewing(saved || null); // land on the detail view after saving
      setTimeout(() => setSaveState("idle"), 1200);
    } catch {
      setSaveState("error");
    }
  };

  const remove = async (id) => {
    if (!window.confirm("Delete this project and its job log? This affects the whole team.")) return;
    setSaveState("saving");
    try {
      await api.deleteProject(id);
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
        <HardHat size={28} style={{ color: SEA }} className="mx-auto mb-3" />
        <h2 className="text-xl font-bold mb-2" style={{ fontFamily: "Georgia, serif" }}>Star Projects</h2>
        <p className="text-[15px] mb-5" style={{ color: "#4a5a60" }}>The commercial job board. Sign in with your Star account to view and edit.</p>
        <a href={api.authLoginUrl()} className="inline-flex items-center gap-2 px-5 py-2.5 rounded text-white text-sm font-medium" style={{ background: INK }}>Sign in with Microsoft</a>
      </section>
    );
  }
  if (projects === null) {
    return <div className="py-16 text-center font-mono text-sm tracking-widest uppercase" style={{ color: SEA }}>Loading projects…</div>;
  }

  if (editing) {
    return (
      <ProjectForm
        initial={editing === "new" ? blankProject : toForm(editing)}
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
      <ProjectDetail
        project={viewing}
        onBack={() => setViewing(null)}
        onEdit={() => setEditing(viewing)}
        onDelete={remove}
        onChanged={(p) => { setViewing(p); refresh(); }}
      />
    );
  }

  return (
    <div>
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div className="relative flex-1 min-w-56">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "#8b9a9f" }} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search job, client, PM, address, material"
            className="w-full bg-white border rounded pl-9 pr-3 py-2 text-sm" style={{ borderColor: BORDER }} />
        </div>
        <div className="flex shrink-0 gap-0.5 p-0.5 rounded bg-white" style={{ border: "1px solid " + BORDER }}>
          {[["all", "All"], ["mine", "My projects"], ["unassigned", "Unassigned"]].map(([k, label]) => (
            <button key={k} onClick={() => setView(k)} className="px-3 py-1.5 text-sm font-medium rounded whitespace-nowrap"
              style={view === k ? { background: INK, color: "white" } : { background: "white", color: INK }}>{label}</button>
          ))}
        </div>
        <span className="font-mono text-xs" style={{ color: saveState === "error" ? TIDE : SEA }}>
          {saveState === "saving" ? "saving…" : saveState === "saved" ? "saved ✓" : saveState === "error" ? "save failed" : ""}
        </span>
        <button onClick={() => setEditing("new")} className="flex items-center gap-1.5 px-3 py-2 rounded text-white text-sm font-medium shrink-0" style={{ background: INK }}>
          <Plus size={16} /> New project
        </button>
      </div>

      <div className="text-xs mb-3 flex items-center gap-2 flex-wrap" style={{ color: "#8b9a9f" }}>
        <span>
          {liveCount} active job{liveCount === 1 ? "" : "s"}
          {view === "mine" ? " assigned to you" : view === "unassigned" ? " with no PM" : ""}
          {" · "}{filtered.length} total
        </span>
        {lateCount > 0 && (
          <span className="inline-flex items-center gap-1 font-medium" style={{ color: TIDE }}>
            <AlertTriangle size={12} /> {lateCount} past target
          </span>
        )}
      </div>

      {/* Stage groups */}
      <div className="space-y-3">
        {STAGES.map((st) => {
          const rows = grouped[st.key] || [];
          const isCollapsed = collapsed.has(st.key);
          if (rows.length === 0 && !st.main) return null; // hide empty side stages
          return (
            <section key={st.key} className="bg-white rounded-lg border overflow-hidden" style={{ borderColor: BORDER }}>
              <button onClick={() => toggleStage(st.key)} className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-stone-50"
                style={{ borderBottom: isCollapsed ? "none" : "1px solid " + ROW_LINE }}>
                {isCollapsed ? <ChevronRight size={15} style={{ color: "#8b9a9f" }} /> : <ChevronDown size={15} style={{ color: "#8b9a9f" }} />}
                <span className="font-mono text-[10px] font-bold px-2 py-0.5 rounded-full whitespace-nowrap" style={{ background: st.bg, color: st.fg }}>
                  {st.label}
                </span>
                <span className="font-mono text-xs tabular-nums" style={{ color: "#8b9a9f" }}>{rows.length}</span>
                {st.main && <span className="text-[10px] font-mono uppercase tracking-widest ml-auto" style={{ color: "#b0b8ba" }}>main</span>}
              </button>

              {!isCollapsed && (
                rows.length === 0 ? (
                  <div className="px-3 py-6 text-sm text-center" style={{ color: "#b0b8ba" }}>
                    Nothing in this stage{query.trim() || view !== "all" ? " matches" : ""} yet.
                  </div>
                ) : (
                  <ul>
                    {rows.map((p) => {
                      const urg = urgencyOf(p);
                      return (
                        <li key={p.id}>
                          <button onClick={() => setViewing(p)} className="w-full text-left px-3 py-2.5 hover:bg-stone-50 flex items-start gap-3 flex-wrap"
                            style={{ borderBottom: "1px solid " + ROW_LINE }}>
                            <div className="flex-1 min-w-52">
                              <div className="flex items-center gap-2 flex-wrap">
                                <span className="font-semibold break-words" style={{ color: INK }}>{p.name}</span>
                                <span className="font-mono text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded" style={{ background: MIST, color: "#6b7a80" }}>
                                  {TYPES[p.projectType] || "Other"}
                                </span>
                              </div>
                              <div className="text-xs mt-0.5 flex items-center gap-2 flex-wrap" style={{ color: "#8b9a9f" }}>
                                {p.client && <span>{p.client}</span>}
                                {p.siteAddress && <span className="inline-flex items-center gap-1"><MapPin size={11} />{p.siteAddress}</span>}
                              </div>
                              {p.log?.[0] && (
                                <div className="text-xs mt-1 truncate" style={{ color: "#6b7a80", maxWidth: "38rem" }}>
                                  <span className="font-mono" style={{ color: SEA }}>{p.log[0].date}</span>{" "}{p.log[0].note}
                                </div>
                              )}
                            </div>

                            <div className="flex items-center gap-3 shrink-0 ml-auto">
                              {p.sqFt != null && (
                                <span className="font-mono text-[11px] tabular-nums hidden sm:inline" style={{ color: "#8b9a9f" }}>
                                  {p.sqFt.toLocaleString()} sf
                                </span>
                              )}
                              {p.contractValue != null && (
                                <span className="font-mono text-[12px] tabular-nums font-semibold" style={{ color: INK }}>
                                  {fmtMoney(p.contractValue)}
                                </span>
                              )}
                              <span className="text-right min-w-20">
                                {p.targetDate ? (
                                  <>
                                    <span className="font-mono text-[12px] tabular-nums block" style={{ color: urg ? urg.color : "#6b7a80" }}>
                                      {fmtDate(p.targetDate)}
                                    </span>
                                    {urg && <span className="text-[10px] font-medium block" style={{ color: urg.color }}>{urg.label}</span>}
                                  </>
                                ) : <span className="text-xs" style={{ color: "#d0d6d8" }}>no target</span>}
                              </span>
                              {p.pm ? (
                                <span className="w-6 h-6 rounded-full text-white text-[9px] font-bold flex items-center justify-center shrink-0"
                                  style={{ background: pmColor(p.pm) }} title={p.pm}>{pmInitials(p.pm)}</span>
                              ) : (
                                <span className="w-6 h-6 rounded-full text-[9px] font-bold flex items-center justify-center shrink-0"
                                  style={{ background: MIST, color: "#b0b8ba" }} title="Unassigned">—</span>
                              )}
                            </div>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )
              )}
            </section>
          );
        })}
      </div>

      {filtered.length === 0 && (
        <div className="text-sm text-center py-10" style={{ color: "#8b9a9f" }}>
          {projects.length === 0
            ? "No projects yet. Add the first job with “New project”."
            : "No projects match. Clear the search or switch views."}
        </div>
      )}
    </div>
  );
}

// Map a serialized project to the editable form shape (numbers become strings
// so the inputs stay controlled and an empty field means "not set").
function toForm(p) {
  return {
    id: p.id,
    name: p.name || "",
    client: p.client || "",
    siteAddress: p.siteAddress || "",
    projectType: p.projectType || "other",
    stage: p.stage || "in_progress",
    pm: p.pm || "",
    contractValue: p.contractValue == null ? "" : String(p.contractValue),
    startDate: p.startDate || "",
    targetDate: p.targetDate || "",
    material: p.material || "",
    sqFt: p.sqFt == null ? "" : String(p.sqFt),
    description: p.description || "",
  };
}

// Rebuild a full PUT payload from a serialized project plus field overrides —
// lets the detail view patch one thing (stage, description) without opening
// the whole form.
function projectToPayload(p, overrides = {}) {
  return {
    name: p.name,
    client: p.client || "",
    siteAddress: p.siteAddress || "",
    projectType: p.projectType || "other",
    stage: p.stage || "in_progress",
    pm: p.pm || "",
    contractValue: p.contractValue,
    startDate: p.startDate || "",
    targetDate: p.targetDate || "",
    material: p.material || "",
    sqFt: p.sqFt,
    description: p.description || "",
    ...overrides,
  };
}

function ProjectForm({ initial, users, onCancel, onSave, onDelete, saveState }) {
  const [f, setF] = useState(initial);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  const field = "w-full bg-white border rounded px-3 py-2 text-sm";
  const bc = { borderColor: BORDER };
  const cap = "font-mono text-[10px] uppercase tracking-widest block mb-1";

  // Guard against a double-submit creating two rows (the CRM bug from
  // 2026-08-11): await the save and disable the button while it's in flight.
  const submit = async () => {
    if (busy || !f.name.trim()) return;
    setBusy(true);
    try { await onSave(f); }
    finally { setBusy(false); }
  };

  return (
    <section className="bg-white rounded-lg p-5 border-l-4 max-w-3xl" style={{ borderColor: SEA }}>
      <div className="flex items-center justify-between mb-4">
        <button onClick={onCancel} className="flex items-center gap-1 font-mono text-xs uppercase tracking-widest" style={{ color: SEA }}>
          <ChevronLeft size={14} /> Back
        </button>
        {f.id && (
          <button onClick={() => onDelete(f.id)} className="p-2 rounded hover:bg-stone-100" style={{ color: TIDE }} title="Delete project">
            <Trash2 size={16} />
          </button>
        )}
      </div>

      <h2 className="text-xl font-bold mb-4" style={{ fontFamily: "Georgia, serif" }}>
        {f.id ? "Edit project" : "New project"}
      </h2>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label className={cap} style={{ color: SEA }}>Job name</label>
          <input autoFocus className={field} style={bc} maxLength={200} autoComplete="off"
            placeholder="Lincoln High — gym & corridors" value={f.name} onChange={set("name")} />
        </div>
        <div>
          <label className={cap} style={{ color: SEA }}>Client (GC / owner)</label>
          <input className={field} style={bc} maxLength={200} autoComplete="off"
            placeholder="Balfour Beatty" value={f.client} onChange={set("client")} />
        </div>
        <div>
          <label className={cap} style={{ color: SEA }}>Project type</label>
          <select className={field} style={bc} value={f.projectType} onChange={set("projectType")}>
            {TYPE_KEYS.map((k) => <option key={k} value={k}>{TYPES[k]}</option>)}
          </select>
        </div>
        <div className="sm:col-span-2">
          <label className={cap} style={{ color: SEA }}>Site address</label>
          <input className={field} style={bc} maxLength={300} autoComplete="off"
            placeholder="4304 Euclid Ave, San Diego, CA 92115" value={f.siteAddress} onChange={set("siteAddress")} />
        </div>
        <div>
          <label className={cap} style={{ color: SEA }}>Stage</label>
          <select className={field} style={bc} value={f.stage} onChange={set("stage")}>
            {STAGES.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </div>
        <div>
          <label className={cap} style={{ color: SEA }}>Project manager</label>
          {/* Free text + datalist: type-and-autofill from known profiles, but a
              custom or shared "Marc / RJ" value is still allowed. */}
          <input className={field} style={bc} maxLength={120} autoComplete="off" list="pm-options"
            placeholder="Name" value={f.pm} onChange={set("pm")} />
          <datalist id="pm-options">
            {users.map((u) => <option key={u.id} value={u.name} />)}
          </datalist>
        </div>
        <div>
          <label className={cap} style={{ color: SEA }}>Start date</label>
          <input type="date" className={field} style={bc} value={f.startDate} onChange={set("startDate")} />
        </div>
        <div>
          <label className={cap} style={{ color: SEA }}>Target completion</label>
          <input type="date" className={field} style={bc} value={f.targetDate} onChange={set("targetDate")} />
        </div>
        <div>
          <label className={cap} style={{ color: SEA }}>Contract value ($)</label>
          <input type="number" min="0" step="1" className={field} style={bc} autoComplete="off"
            placeholder="185000" value={f.contractValue} onChange={set("contractValue")} />
        </div>
        <div>
          <label className={cap} style={{ color: SEA }}>Square feet</label>
          <input type="number" min="0" step="1" className={field} style={bc} autoComplete="off"
            placeholder="12400" value={f.sqFt} onChange={set("sqFt")} />
        </div>
        <div className="sm:col-span-2">
          <label className={cap} style={{ color: SEA }}>Material / scope</label>
          <input className={field} style={bc} maxLength={120} autoComplete="off"
            placeholder="LVP + broadloom carpet, tile in restrooms" value={f.material} onChange={set("material")} />
        </div>
        <div className="sm:col-span-2">
          <label className={cap} style={{ color: SEA }}>Description</label>
          <textarea className={field + " h-28"} style={bc} maxLength={5000}
            placeholder="Scope, phasing, access constraints, who to call on site…"
            value={f.description} onChange={set("description")} />
        </div>
      </div>

      <div className="flex items-center gap-2 mt-5">
        <button onClick={submit} disabled={busy || !f.name.trim()}
          className="px-4 py-2 rounded text-white text-sm font-medium disabled:opacity-50" style={{ background: INK }}>
          {busy || saveState === "saving" ? "Saving…" : f.id ? "Save project" : "Create project"}
        </button>
        <button onClick={onCancel} className="px-4 py-2 rounded text-sm" style={{ background: MIST }}>Cancel</button>
        {saveState === "error" && <span className="text-xs" style={{ color: TIDE }}>Save failed. Try again.</span>}
      </div>
    </section>
  );
}

function ProjectDetail({ project, onBack, onEdit, onDelete, onChanged }) {
  const [proj, setProj] = useState(project);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [editDesc, setEditDesc] = useState(false);
  const [descDraft, setDescDraft] = useState(project.description || "");

  useEffect(() => { setProj(project); setDescDraft(project.description || ""); }, [project]);

  const st = STAGE_BY_KEY[proj.stage] || STAGE_BY_KEY.in_progress;
  const urg = urgencyOf(proj);
  const field = "w-full bg-white border rounded px-3 py-2 text-sm";
  const bc = { borderColor: BORDER };
  const cap = "font-mono text-[10px] uppercase tracking-widest block mb-1";

  const put = async (overrides) => {
    setBusy(true);
    try {
      const updated = await api.updateProject(proj.id, projectToPayload(proj, overrides));
      setProj(updated); onChanged(updated);
      return true;
    } catch { return false; }
    finally { setBusy(false); }
  };

  const logNote = async () => {
    const n = note.trim();
    if (!n || busy) return;
    setBusy(true);
    try {
      const updated = await api.logProjectNote(proj.id, n);
      setProj(updated); onChanged(updated); setNote("");
    } catch { /* ignore */ }
    finally { setBusy(false); }
  };
  const saveDesc = async () => { if (await put({ description: descDraft })) setEditDesc(false); };

  const Meta = ({ icon, label, value }) => (
    <div>
      <div className={cap} style={{ color: SEA }}>{label}</div>
      <div className="text-sm flex items-center gap-1.5" style={{ color: value ? INK : "#b0b8ba" }}>
        {value ? icon : null}{value || "—"}
      </div>
    </div>
  );

  return (
    <section className="bg-white rounded-lg p-5 border-l-4 max-w-3xl" style={{ borderColor: SEA }}>
      <div className="flex items-center justify-between mb-3">
        <button onClick={onBack} className="flex items-center gap-1 font-mono text-xs uppercase tracking-widest" style={{ color: SEA }}>
          <ChevronLeft size={14} /> All projects
        </button>
        <div className="flex items-center gap-2">
          <button onClick={onEdit} className="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium"
            style={{ background: "white", color: INK, border: "1px solid " + BORDER }}>
            <Pencil size={14} /> Edit
          </button>
          <button onClick={() => onDelete(proj.id)} className="p-2 rounded hover:bg-stone-100" style={{ color: TIDE }} title="Delete project">
            <Trash2 size={16} />
          </button>
        </div>
      </div>

      <div className="flex items-start justify-between gap-3 flex-wrap mb-1">
        <h2 className="text-2xl font-bold break-words" style={{ fontFamily: "Georgia, serif", maxWidth: "34rem" }}>{proj.name}</h2>
        <span className="font-mono text-[10px] font-bold px-2 py-0.5 rounded-full" style={{ background: st.bg, color: st.fg }}>{st.label}</span>
      </div>

      <div className="flex items-center gap-3 flex-wrap text-sm mb-2" style={{ color: "#4a5a60" }}>
        <span className="font-mono text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded" style={{ background: MIST, color: "#6b7a80" }}>
          {TYPES[proj.projectType] || "Other"}
        </span>
        {proj.client && <span>{proj.client}</span>}
        {proj.pm
          ? <span className="inline-flex items-center gap-1.5">
              <span className="w-5 h-5 rounded-full text-white text-[9px] font-bold flex items-center justify-center" style={{ background: pmColor(proj.pm) }}>{pmInitials(proj.pm)}</span>
              {proj.pm}
            </span>
          : <span style={{ color: "#b0b8ba" }}>No PM assigned</span>}
      </div>

      {urg && (
        <div className="inline-flex items-center gap-1.5 text-sm font-medium mb-3 px-2 py-1 rounded"
          style={{ color: urg.color, background: "rgba(192,57,43,.07)" }}>
          <AlertTriangle size={13} /> Target {fmtDate(proj.targetDate)} — {urg.label}
        </div>
      )}

      {/* Quick stage change: the one edit a PM makes constantly, so it doesn't
          need the full form. */}
      <div className="mb-4">
        <div className={cap} style={{ color: SEA }}>Move stage</div>
        <div className="flex flex-wrap gap-1">
          {STAGES.map((s) => (
            <button key={s.key} disabled={busy || s.key === proj.stage} onClick={() => put({ stage: s.key })}
              className="font-mono text-[10px] font-bold px-2 py-1 rounded-full disabled:cursor-default"
              style={s.key === proj.stage
                ? { background: s.bg, color: s.fg, outline: "2px solid " + INK, outlineOffset: "1px" }
                : { background: MIST, color: "#6b7a80" }}>
              {s.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-3 mb-4 pt-3" style={{ borderTop: "1px solid " + ROW_LINE }}>
        <Meta label="Site" icon={<MapPin size={13} style={{ color: SEA }} />} value={proj.siteAddress} />
        <Meta label="Start" icon={<Calendar size={13} style={{ color: SEA }} />} value={fmtDate(proj.startDate)} />
        <Meta label="Target completion" icon={<Calendar size={13} style={{ color: SEA }} />} value={fmtDate(proj.targetDate)} />
        <Meta label="Contract value" value={fmtMoney(proj.contractValue)} />
        <Meta label="Square feet" icon={<Ruler size={13} style={{ color: SEA }} />} value={proj.sqFt != null ? proj.sqFt.toLocaleString() + " sf" : ""} />
        <Meta label="Material / scope" value={proj.material} />
      </div>

      <div className="mb-5">
        <div className="flex items-center justify-between mb-1">
          <div className={cap} style={{ color: SEA }}>Description</div>
          {!editDesc && (
            <button onClick={() => { setDescDraft(proj.description || ""); setEditDesc(true); }} className="text-xs font-medium flex items-center gap-1" style={{ color: SEA }}>
              <Pencil size={12} /> Edit
            </button>
          )}
        </div>
        {editDesc ? (
          <div>
            <textarea className={field + " h-28"} style={bc} maxLength={5000} value={descDraft} onChange={(e) => setDescDraft(e.target.value)} />
            <div className="flex gap-2 mt-2">
              <button onClick={saveDesc} disabled={busy} className="px-3 py-1.5 rounded text-white text-sm" style={{ background: SEA }}>Save description</button>
              <button onClick={() => setEditDesc(false)} className="px-3 py-1.5 rounded text-sm" style={{ background: MIST }}>Cancel</button>
            </div>
          </div>
        ) : (
          proj.description
            ? <p className="text-[15px] leading-relaxed whitespace-pre-wrap" style={{ color: INK }}>{proj.description}</p>
            : <div className="text-sm" style={{ color: "#b0b8ba" }}>No description yet.</div>
        )}
      </div>

      <div>
        <div className="font-mono text-xs uppercase tracking-widest mb-2" style={{ color: INK }}>Job log</div>
        <div className="flex gap-2">
          <input value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={(e) => e.key === "Enter" && logNote()}
            placeholder="Log an update — site visit, delivery, delay, inspection…" maxLength={2000} autoComplete="off"
            className="flex-1 border rounded px-3 py-2 text-sm bg-white" style={bc} />
          <button onClick={logNote} disabled={busy || !note.trim()}
            className="px-3 py-2 rounded text-white text-sm font-medium disabled:opacity-50 shrink-0" style={{ background: INK }}>Log note</button>
        </div>
        <ul className="mt-3 space-y-2">
          {(proj.log || []).map((l, i) => (
            <li key={i} className="flex gap-3 text-sm">
              <span className="font-mono text-xs pt-0.5 shrink-0" style={{ color: SEA }}>{l.date}</span>
              <span>{l.note}{l.by ? <span className="text-xs ml-1" style={{ color: "#b0b8ba" }}>— {l.by}</span> : null}</span>
            </li>
          ))}
          {(proj.log || []).length === 0 && (
            <li className="text-sm" style={{ color: "#8b9a9f" }}>No log entries yet. The first note is the start of this job's history.</li>
          )}
        </ul>
      </div>
    </section>
  );
}
