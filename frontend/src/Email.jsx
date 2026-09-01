// Email tab: thread-level triage of the signed-in user's own inbox.
//
// Four lanes — Needs your reply / Waiting on them / Worth knowing / Cleanup
// candidates — each row carrying the REASON it ranked where it did, because a
// ranking you can't audit is a ranking you can't trust. Resolved threads are
// deliberately absent: their absence IS the feature (the closed-ticket-
// presented-as-work failure this tab exists to kill). Rows open in Outlook;
// this tab never tries to become a mail client, and phase 1 writes nothing
// back (no send, no delete — the cleanup lane is read-only until Mail.ReadWrite
// lands).
import { useEffect, useRef, useState } from "react";
import {
  Inbox, RefreshCw, ExternalLink, Flag, Sparkles, ChevronDown, ChevronRight,
  Building2, CheckCircle2, X, Undo2, ArrowRight, Search,
} from "lucide-react";
import * as api from "./api.js";

const INK = "#1C1C1C";
const MIST = "#F3F0EC";
const SEA = "#922525";
const TIDE = "#C0392B";
const BORDER = "#cdd6d4";
const ROW_LINE = "#e4dfd3";

const LANES = [
  {
    key: "needsReply", label: "Needs your reply",
    bg: "rgba(146,37,37,.10)", fg: SEA, open: true,
    empty: "Nothing waiting on you. Clean slate.",
  },
  {
    key: "fyi", label: "Worth knowing",
    bg: "rgba(140,109,70,.16)", fg: "#8C6D46", open: true,
    empty: "Nothing informational right now.",
    note: "Cold outreach, ticket updates, and low-urgency items — visible so nothing surprises you, but not treated as tasks.",
  },
  {
    key: "cleanup", label: "Cleanup candidates",
    bg: "#ece7df", fg: "#6b7a80", open: false,
    empty: "No bulk mail detected.",
    note: "Newsletters and notifications the filter caught. Reviewing and moving these to trash arrives in a later update.",
  },
  {
    key: "dismissed", label: "Dismissed",
    bg: "rgba(62,76,89,.14)", fg: "#3E4C59", open: false,
    empty: "Nothing dismissed yet.",
    note: "Threads you hid from the reply lane. Any new message on one of these auto-clears the dismiss.",
  },
];

const COLLAPSE_KEY = "starEmailCollapsed";

const CATEGORY = {
  customer: { label: "Customer", bg: "rgba(47,93,80,.16)", fg: "#2b6a58" },
  vendor: { label: "Vendor", bg: "rgba(140,109,70,.16)", fg: "#8C6D46" },
  internal: { label: "Internal", bg: "rgba(62,76,89,.14)", fg: "#3E4C59" },
  notification: { label: "Notification", bg: "#ece7df", fg: "#8b9a9f" },
  other: { label: "", bg: "", fg: "" },
};

// Age since the thread last moved, compact ("3h", "5d").
const ageOf = (iso) => {
  if (!iso) return "";
  const then = new Date(iso);
  if (isNaN(then)) return "";
  const mins = Math.max(0, Math.round((Date.now() - then.getTime()) / 60000));
  if (mins < 60) return `${mins}m`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h`;
  return `${Math.round(mins / (60 * 24))}d`;
};

const fmtTime = (iso) => {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  if (isNaN(d)) return "";
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
};

export default function EmailTab() {
  const [me, setMe] = useState(() => api.getCachedMe() ?? null);
  const [data, setData] = useState(null);      // overview payload
  const [syncing, setSyncing] = useState(false);
  const [firstScan, setFirstScan] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");  // all | needsReply | fyi | cleanup | dismissed
  const [sort, setSort] = useState("importance"); // importance | newest | oldest
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(() => new Set()); // ids checked for bulk actions
  // Selection resets when the filter or search changes. This useEffect MUST
  // live above the early-return gates below — otherwise the hook order
  // changes across renders and React blanks the page (Rules of Hooks).
  useEffect(() => { setSelected(new Set()); }, [filter, query]);
  const [collapsed, setCollapsed] = useState(() => {
    try {
      const raw = JSON.parse(localStorage.getItem(COLLAPSE_KEY) || "null");
      if (Array.isArray(raw)) return new Set(raw);
    } catch { /* defaults below */ }
    return new Set(LANES.filter((l) => !l.open).map((l) => l.key));
  });
  const syncedOnce = useRef(false);

  useEffect(() => {
    if (me) return;
    api.authMe().then(setMe).catch(() => setMe({ signedIn: false, configured: true }));
  }, []);

  const runSync = async (isFirst) => {
    if (isFirst) setFirstScan(true);
    setSyncing(true);
    setError("");
    try {
      const out = await api.emailSync();
      setData(out);
    } catch (e) {
      setError(e?.message || "Sync failed — try again.");
    } finally {
      setSyncing(false);
      setFirstScan(false);
    }
  };

  useEffect(() => {
    if (!me?.signedIn) return;
    api.emailOverview()
      .then((out) => {
        setData(out);
        // Never synced -> kick off the first backfill automatically so the
        // tab isn't an empty page with a button on first visit.
        if (!out.lastSync && !syncedOnce.current) {
          syncedOnce.current = true;
          runSync(true);
        }
      })
      .catch(() => setError("Couldn't load the email overview."));
  }, [me?.signedIn]);

  const toggleLane = (key) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...next])); } catch { /* ignore */ }
      return next;
    });
  };

  const toggleDigest = async () => {
    if (!data) return;
    const want = !data.digestEnabled;
    setData({ ...data, digestEnabled: want }); // optimistic
    try {
      await api.setEmailPrefs({ digestEnabled: want });
    } catch {
      setData((d) => ({ ...d, digestEnabled: !want })); // revert on failure
    }
  };

  // --- Gates -----------------------------------------------------------------
  if (me === null) {
    return <div className="py-16 text-center font-mono text-sm tracking-widest uppercase" style={{ color: SEA }}>Checking sign-in…</div>;
  }
  if (!me.signedIn) {
    return (
      <section className="bg-white rounded-lg p-8 text-center border-l-4 max-w-lg mx-auto mt-8" style={{ borderColor: SEA }}>
        <Inbox size={28} style={{ color: SEA }} className="mx-auto mb-3" />
        <h2 className="text-xl font-bold mb-2" style={{ fontFamily: "Georgia, serif" }}>Star Mail</h2>
        <p className="text-[15px] mb-5" style={{ color: "#4a5a60" }}>
          Your inbox, ranked — what needs you, what's waiting on them, and what can go.
          Sign in with your Star account.
        </p>
        <a href={api.authLoginUrl()} className="inline-flex items-center gap-2 px-5 py-2.5 rounded text-white text-sm font-medium" style={{ background: INK }}>Sign in with Microsoft</a>
      </section>
    );
  }
  if (data === null && !error) {
    return <div className="py-16 text-center font-mono text-sm tracking-widest uppercase" style={{ color: SEA }}>Loading…</div>;
  }

  const lanes = data?.lanes || { needsReply: [], fyi: [], cleanup: [], dismissed: [] };
  const needCount = lanes.needsReply.length;

  const dismiss = async (id) => {
    // Optimistic: move the thread into the dismissed bucket immediately, so
    // the row disappears from wherever it lived. Kept in front-end state
    // (not a refetch) so a stack of dismisses in a row stays smooth.
    setData((d) => {
      if (!d) return d;
      const next = { ...d, lanes: { ...d.lanes } };
      let moved = null;
      for (const k of ["needsReply", "fyi", "cleanup"]) {
        const before = next.lanes[k] || [];
        next.lanes[k] = before.filter((t) => {
          if (t.id === id) { moved = { ...t, dismissed: true }; return false; }
          return true;
        });
      }
      if (moved) next.lanes.dismissed = [moved, ...(next.lanes.dismissed || [])];
      return next;
    });
    try { await api.dismissEmailThread(id); }
    catch { runSync(false); /* recover from server truth */ }
  };

  // --- Bulk-dismiss selection --------------------------------------------
  const toggleSelected = (id) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const clearSelection = () => setSelected(new Set());
  // (The useEffect that clears selection on filter/query change lives above
  // the early-return gates — hooks must run in the same order every render.)

  const bulkDismiss = async () => {
    const ids = [...selected];
    if (ids.length === 0) return;
    // Optimistic: move all selected rows to the dismissed bucket in one pass.
    setData((d) => {
      if (!d) return d;
      const next = { ...d, lanes: { ...d.lanes } };
      const moved = [];
      for (const k of ["needsReply", "fyi", "cleanup"]) {
        const before = next.lanes[k] || [];
        next.lanes[k] = before.filter((t) => {
          if (selected.has(t.id)) { moved.push({ ...t, dismissed: true }); return false; }
          return true;
        });
      }
      next.lanes.dismissed = [...moved, ...(next.lanes.dismissed || [])];
      return next;
    });
    clearSelection();
    // Sequential POSTs — the /dismiss endpoint is cheap (single UPDATE) and
    // 20-30 requests in flight would be wasteful, not faster.
    for (const id of ids) {
      try { await api.dismissEmailThread(id); }
      catch { /* recover from server truth after the loop */ }
    }
  };

  const undismiss = async (id) => {
    // Route back by state — we know the row's state from its payload.
    setData((d) => {
      if (!d) return d;
      const next = { ...d, lanes: { ...d.lanes } };
      let moved = null;
      next.lanes.dismissed = (next.lanes.dismissed || []).filter((t) => {
        if (t.id === id) { moved = { ...t, dismissed: false }; return false; }
        return true;
      });
      if (moved) {
        const target = moved.state === "needs_reply" ? "needsReply"
                     : moved.state === "bulk" ? "cleanup" : "fyi";
        next.lanes[target] = [moved, ...(next.lanes[target] || [])];
      }
      return next;
    });
    try { await api.undismissEmailThread(id); }
    catch { runSync(false); }
  };

  // Filter chips: match the CRM/Accounts segmented-control pattern. `all`
  // keeps the multi-lane view; anything else collapses to a flat rank-sorted
  // list for that one bucket. Counts on each chip mirror lane row counts so
  // an empty bucket is visible before you click.
  const FILTERS = [
    { key: "all", label: "All", n: (lanes.needsReply?.length || 0) + (lanes.fyi?.length || 0) + (lanes.cleanup?.length || 0) },
    { key: "needsReply", label: "Reply needed", n: lanes.needsReply?.length || 0 },
    { key: "fyi", label: "Worth knowing", n: lanes.fyi?.length || 0 },
    { key: "cleanup", label: "Cleanup", n: lanes.cleanup?.length || 0 },
    { key: "dismissed", label: "Dismissed", n: lanes.dismissed?.length || 0 },
  ];

  // Search runs client-side over rendered rows — cheap over ~200 threads.
  const q = query.trim().toLowerCase();
  const matches = (t) => !q || [
    t.senderName, t.senderEmail, t.subject, t.snippet, t.accountName, t.folder, t.reason,
  ].some((s) => (s || "").toLowerCase().includes(q));

  // Sort comparator honoring the user's choice. `importance` is the classic
  // triage view (rank desc, newest as tie-breaker); `newest` and `oldest`
  // are pure date sorts — useful when a real 30-day-old high-rank ends up
  // pinned to the top and the user wants a chronological view instead.
  const cmp = (a, b) => {
    if (sort === "newest") return (b.lastAt || "").localeCompare(a.lastAt || "");
    if (sort === "oldest") return (a.lastAt || "").localeCompare(b.lastAt || "");
    return b.rank - a.rank || (b.lastAt || "").localeCompare(a.lastAt || "");
  };
  const laneRows = (key) => [...(lanes[key] || []).filter(matches)].sort(cmp);
  const flatRows = filter === "all" ? null : laneRows(filter);
  const visibleLanes = filter === "all"
    ? LANES.map((lane) => ({ ...lane, rows: laneRows(lane.key) }))
    : null;

  const filteredNeed = laneRows("needsReply").length;
  const filteredFyi = laneRows("fyi").length;
  const handledTotal = (data?.resolvedCount || 0);
  const handledClosed = (data?.resolvedClosed || 0);
  const handledWaiting = (data?.resolvedWaiting ?? Math.max(0, handledTotal - handledClosed));

  return (
    <div>
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <div className="relative flex-1 min-w-56">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "#8b9a9f" }} />
          <input value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder="Search sender, subject, snippet, folder"
            className="w-full bg-white border rounded pl-9 pr-3 py-2 text-sm"
            style={{ borderColor: BORDER }} />
        </div>
        <button onClick={() => runSync(false)} disabled={syncing}
          className="flex items-center gap-1.5 px-3 py-2 rounded text-white text-sm font-medium disabled:opacity-60 shrink-0"
          style={{ background: INK }}>
          <RefreshCw size={14} className={syncing ? "animate-spin" : ""} />
          {syncing ? "Scanning…" : "Refresh"}
        </button>
        {data?.lastSync && !syncing && (
          <span className="font-mono text-xs shrink-0" style={{ color: "#8b9a9f" }}>Updated {fmtTime(data.lastSync)}</span>
        )}
        {error && <span className="text-xs" style={{ color: TIDE }}>{error}</span>}

        {/* Digest opt-in — stored now, honoured when the digest ships. */}
        <label className="flex items-center gap-2 ml-auto cursor-pointer select-none">
          <span className="text-xs" style={{ color: "#4a5a60" }}>Daily digest email</span>
          <button role="switch" aria-checked={!!data?.digestEnabled} onClick={toggleDigest}
            className="relative w-9 h-5 rounded-full transition-colors"
            style={{ background: data?.digestEnabled ? "#2F5D50" : "#cdd6d4" }}>
            <span className="absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all"
              style={{ left: data?.digestEnabled ? "18px" : "2px" }} />
          </button>
          <span className="font-mono text-[10px] uppercase" style={{ color: "#b0b8ba" }}>coming soon</span>
        </label>
      </div>

      {/* Filter chips + sort control */}
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <div className="flex flex-wrap gap-0.5 p-0.5 rounded bg-white w-fit"
          style={{ border: "1px solid " + BORDER }}>
          {FILTERS.map((f) => (
            <button key={f.key} onClick={() => setFilter(f.key)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded whitespace-nowrap"
              style={filter === f.key ? { background: INK, color: "white" } : { background: "white", color: INK }}>
              {f.label}
              <span className="font-mono text-[10px] tabular-nums"
                style={{ color: filter === f.key ? "rgba(255,255,255,.75)" : "#8b9a9f" }}>{f.n}</span>
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1">
          <span className="font-mono text-[10px] uppercase tracking-widest" style={{ color: "#8b9a9f" }}>Sort</span>
          <div className="flex gap-0.5 p-0.5 rounded bg-white" style={{ border: "1px solid " + BORDER }}>
            {[
              { k: "importance", label: "Importance" },
              { k: "newest", label: "Newest" },
              { k: "oldest", label: "Oldest" },
            ].map((s) => (
              <button key={s.k} onClick={() => setSort(s.k)}
                className="px-2.5 py-1 text-xs font-medium rounded whitespace-nowrap"
                style={sort === s.k ? { background: INK, color: "white" } : { background: "white", color: INK }}>
                {s.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Counts line — split "handled" into its two real meanings. */}
      <div className="text-xs mb-4 flex items-center gap-3 flex-wrap" style={{ color: "#8b9a9f" }}>
        <span>
          {filteredNeed === 0 ? "Nothing needs your reply" : `${filteredNeed} thread${filteredNeed === 1 ? "" : "s"} need${filteredNeed === 1 ? "s" : ""} your reply`}
          {" · "}{filteredFyi} worth knowing
          {q && <span className="ml-1 italic" style={{ color: SEA }}>(filtered by "{query}")</span>}
        </span>
        {handledTotal > 0 && (
          <span className="inline-flex items-center gap-1" title="Threads not shown in the lanes above"
            style={{ color: "#2b6a58" }}>
            <CheckCircle2 size={12} />
            {handledWaiting > 0 && handledClosed > 0
              ? `${handledTotal} handled — ${handledWaiting} you replied, ${handledClosed} closed on their own`
              : handledWaiting > 0
              ? `${handledWaiting} you already replied to`
              : `${handledClosed} closed on their own`}
          </span>
        )}
      </div>

      {firstScan && (
        <div className="rounded-lg border p-4 mb-4 text-sm" style={{ borderColor: BORDER, background: "white", color: "#4a5a60" }}>
          <span className="font-medium" style={{ color: INK }}>First scan of your mailbox.</span>{" "}
          Reading the last 30 days and sorting the threads — this one takes about a minute.
          Every refresh after this is incremental and quick.
        </div>
      )}

      {/* Sticky bulk-action bar — floats above the lanes so it stays visible
          while the user scrolls through checking rows. Only in lanes where
          bulk-dismiss makes sense; cleanup/dismissed excluded. */}
      {selected.size > 0 && (
        <div className="sticky top-2 z-10 mb-3 flex items-center gap-3 px-3 py-2 rounded-lg shadow-md"
          style={{ background: INK, color: "white" }}>
          <span className="text-sm font-medium">{selected.size} selected</span>
          <button onClick={bulkDismiss}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium"
            style={{ background: SEA }}>
            <X size={13} /> Dismiss {selected.size}
          </button>
          <button onClick={clearSelection}
            className="text-sm underline underline-offset-2 opacity-80 hover:opacity-100">
            Clear
          </button>
        </div>
      )}

      {/* Content: multi-lane view for "All", flat rank-ordered list otherwise */}
      {filter === "all" ? (
        <div className="space-y-3">
          {visibleLanes.map((lane) => {
            const isCollapsed = collapsed.has(lane.key);
            return (
              <section key={lane.key} className="bg-white rounded-lg border overflow-hidden" style={{ borderColor: BORDER }}>
                <button onClick={() => toggleLane(lane.key)}
                  className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-stone-50"
                  style={{ borderBottom: isCollapsed ? "none" : "1px solid " + ROW_LINE }}>
                  {isCollapsed ? <ChevronRight size={15} style={{ color: "#8b9a9f" }} /> : <ChevronDown size={15} style={{ color: "#8b9a9f" }} />}
                  <span className="font-mono text-[10px] font-bold px-2 py-0.5 rounded-full whitespace-nowrap"
                    style={{ background: lane.bg, color: lane.fg }}>{lane.label}</span>
                  <span className="font-mono text-xs tabular-nums" style={{ color: "#8b9a9f" }}>{lane.rows.length}</span>
                </button>

                {!isCollapsed && (
                  <>
                    {lane.note && lane.rows.length > 0 && (
                      <div className="px-3 pt-2 text-[11px]" style={{ color: "#b0b8ba" }}>{lane.note}</div>
                    )}
                    {lane.rows.length === 0 ? (
                      <div className="px-3 py-6 text-sm text-center" style={{ color: "#b0b8ba" }}>
                        {q ? "No matches in this lane." : lane.empty}
                      </div>
                    ) : (
                      <ul>
                        {lane.rows.map((t) => {
                          const canBulk = lane.key === "needsReply" || lane.key === "fyi";
                          return (
                            <ThreadRow
                              key={t.id}
                              t={t}
                              onDismiss={lane.key !== "dismissed" && lane.key !== "cleanup" ? () => dismiss(t.id) : null}
                              onUndismiss={lane.key === "dismissed" ? () => undismiss(t.id) : null}
                              checked={selected.has(t.id)}
                              onToggleCheck={canBulk ? () => toggleSelected(t.id) : null}
                            />
                          );
                        })}
                      </ul>
                    )}
                  </>
                )}
              </section>
            );
          })}
        </div>
      ) : (
        // Single-bucket zoom: flat list ordered by rank. Lane note kept as a
        // one-line preamble so context isn't lost when the header is gone.
        (() => {
          const active = LANES.find((l) => l.key === filter);
          return (
            <section className="bg-white rounded-lg border overflow-hidden" style={{ borderColor: BORDER }}>
              {active?.note && (
                <div className="px-3 py-2 text-[11px]" style={{ color: "#b0b8ba", borderBottom: "1px solid " + ROW_LINE }}>
                  {active.note}
                </div>
              )}
              {flatRows.length === 0 ? (
                <div className="px-3 py-8 text-sm text-center" style={{ color: "#b0b8ba" }}>
                  {q ? `No ${active?.label?.toLowerCase() || ""} threads match "${query}".` : active?.empty}
                </div>
              ) : (
                <ul>
                  {flatRows.map((t) => {
                    const canBulk = filter === "needsReply" || filter === "fyi";
                    return (
                      <ThreadRow
                        key={t.id}
                        t={t}
                        onDismiss={filter !== "dismissed" && filter !== "cleanup" ? () => dismiss(t.id) : null}
                        onUndismiss={filter === "dismissed" ? () => undismiss(t.id) : null}
                        checked={selected.has(t.id)}
                        onToggleCheck={canBulk ? () => toggleSelected(t.id) : null}
                      />
                    );
                  })}
                </ul>
              )}
            </section>
          );
        })()
      )}
    </div>
  );
}

function ThreadRow({ t, onDismiss, onUndismiss, checked, onToggleCheck }) {
  const cat = CATEGORY[t.category] || CATEGORY.other;
  // Empty webLink used to render as href="#" which reloaded the SPA and
  // looked like "the CRM tab". Fall back to a plain div in that case so
  // there is no phantom click target.
  const hasLink = !!t.webLink;
  const Row = hasLink ? "a" : "div";
  const rowProps = hasLink
    ? { href: t.webLink, target: "_blank", rel: "noreferrer" }
    : {};
  const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
  // Importance indicator: only marks the top tier. A row's ABSENCE of the
  // dot is already the negative signal; a dim dot on everything else just
  // adds a second glyph per row for no gain. Threshold 65 lines up with
  // the model's "importance 4-5" bucket + the deterministic "strong reply
  // candidate" range.
  const isHighImportance = t.rank >= 65;

  return (
    <li>
      <Row {...rowProps}
        className={"block px-3 py-2.5 " + (hasLink ? "hover:bg-stone-50" : "")}
        style={{ borderBottom: "1px solid " + ROW_LINE, background: checked ? "rgba(146,37,37,.05)" : undefined }}>
        <div className="flex items-start gap-3 flex-wrap">
          {onToggleCheck && (
            <button onClick={(e) => { stop(e); onToggleCheck(); }}
              className="mt-1 shrink-0 w-4 h-4 rounded border flex items-center justify-center"
              style={{
                borderColor: checked ? SEA : BORDER,
                background: checked ? SEA : "white",
              }}
              aria-label={checked ? "Deselect" : "Select for bulk action"}>
              {checked && <span className="text-white text-[10px] leading-none">✓</span>}
            </button>
          )}
          <div className="flex-1 min-w-56">
            <div className="flex items-center gap-2 flex-wrap">
              {isHighImportance && (
                <span
                  title={`High importance (rank ${t.rank})`}
                  className="inline-block w-2 h-2 rounded-full shrink-0"
                  style={{ background: TIDE }}
                />
              )}
              {t.outboundOnly && (
                <ArrowRight size={11} style={{ color: "#8b9a9f" }} title="You started this thread" />
              )}
              <span className="font-semibold text-sm break-words" style={{ color: INK }}>
                {t.senderName || "(no recipient)"}
              </span>
              {t.flagged && <Flag size={11} style={{ color: TIDE }} title="You flagged this" />}
              {t.accountName && (
                <span className="inline-flex items-center gap-1 font-mono text-[9px] font-bold px-1.5 py-0.5 rounded-full"
                  style={{ background: "rgba(146,37,37,.10)", color: SEA }} title="Matches a hit-list account">
                  <Building2 size={9} /> {t.accountName.length > 24 ? t.accountName.slice(0, 24) + "…" : t.accountName}
                </span>
              )}
              {cat.label && (
                <span className="font-mono text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded"
                  style={{ background: cat.bg, color: cat.fg }}>{cat.label}</span>
              )}
              {t.msgCount > 1 && (
                <span className="font-mono text-[10px]" style={{ color: "#b0b8ba" }}>{t.msgCount} msgs</span>
              )}
            </div>
            <div className="text-sm mt-0.5 break-words" style={{ color: "#4a5a60", maxWidth: "44rem" }}>
              {t.subject}
            </div>
            {t.snippet && (
              <div className="text-xs mt-0.5 truncate" style={{ color: "#8b9a9f", maxWidth: "44rem" }}>
                {t.snippet}
              </div>
            )}
            {/* The audit line: WHY it ranked here. */}
            {t.reason && (
              <div className="flex items-center gap-1 mt-1 text-[11px]" style={{ color: SEA }}>
                {t.modelUsed && <Sparkles size={10} title="Judged by the triage model" />}
                <span className="italic">{t.reason}</span>
              </div>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0 ml-auto pt-0.5">
            {t.folder && t.folder.toLowerCase() !== "inbox" && (
              <span className="font-mono text-[10px] px-1.5 py-0.5 rounded" style={{ background: MIST, color: "#6b7a80" }}>{t.folder}</span>
            )}
            <span className="font-mono text-[11px] tabular-nums" style={{ color: "#8b9a9f" }}>{ageOf(t.lastAt)}</span>
            {hasLink && <ExternalLink size={12} style={{ color: "#b0b8ba" }} />}
            {onDismiss && (
              <button onClick={(e) => { stop(e); onDismiss(); }}
                className="p-1 rounded hover:bg-stone-200"
                title="Not a task — hide this thread"
                style={{ color: "#8b9a9f" }}>
                <X size={13} />
              </button>
            )}
            {onUndismiss && (
              <button onClick={(e) => { stop(e); onUndismiss(); }}
                className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono uppercase hover:bg-stone-200"
                title="Restore to its lane"
                style={{ color: SEA }}>
                <Undo2 size={11} /> Restore
              </button>
            )}
          </div>
        </div>
      </Row>
    </li>
  );
}
