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
  Building2, CheckCircle2, X, Undo2, ArrowRight, Search, SlidersHorizontal,
  CornerUpRight, Lightbulb, Check,
} from "lucide-react";
import * as api from "./api.js";
import EmailRules from "./EmailRules.jsx";

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
    bg: "#ece7df", fg: "#55646a", open: false,
    empty: "No bulk mail detected.",
    note: "Newsletters and notifications the filter caught. Reviewing and moving these to trash arrives in a later update.",
  },
  {
    // Added 2026-09-02. Resolved threads used to be counted but never shown,
    // which meant a thread wrongly marked handled was invisible AND
    // uncorrectable — the engine's worst failure had no way to be reported.
    // Collapsed by default, so their absence from the lanes above still does
    // the work; this is a recovery door, not a fifth inbox.
    key: "handled", label: "Handled",
    bg: "rgba(47,93,80,.16)", fg: "#2b6a58", open: false,
    empty: "Nothing closed itself yet.",
    note: "Threads that closed on their own or that you already answered. If one of these still needs you, move it back — that correction is what teaches the triage.",
  },
  {
    key: "dismissed", label: "Dismissed",
    bg: "rgba(62,76,89,.14)", fg: "#3E4C59", open: false,
    empty: "Nothing dismissed yet.",
    note: "Threads you hid from the reply lane. Any new message on one of these auto-clears the dismiss.",
  },
];

// Lane keys as the API's state values, for the row-level move control.
const STATE_OF_LANE = {
  needsReply: "needs_reply", fyi: "fyi", cleanup: "bulk", handled: "resolved",
};
const MOVE_TARGETS = [
  { state: "needs_reply", label: "Needs your reply" },
  { state: "fyi", label: "Worth knowing" },
  { state: "resolved", label: "Handled" },
  { state: "bulk", label: "Cleanup" },
];
// One-tap reasons. Optional, and worth asking for: "not relevant to me" and
// "already handled" imply completely different rules from the same move, so
// this single tap is what lets the suggester generalize correctly.
const WHY_CHIPS = [
  { k: "not_a_task", label: "Not a task" },
  { k: "not_relevant", label: "Not my area" },
  { k: "already_handled", label: "Already handled" },
  { k: "wrong_sender_read", label: "Misread the sender" },
  { k: "is_a_task", label: "This IS a task" },
];

const COLLAPSE_KEY = "starEmailCollapsed";
// Set on a user's first correction. Until then the tab explains the Move
// control once, because the feedback loop is worth nothing if the people it
// learns from never notice the button.
const TAUGHT_KEY = "starEmailTaughtMove";

// A long lane is a lane nobody reads, and an unread lane produces no
// corrections. Ethan's mailbox ran 71 in "Worth knowing" out of 111 visible —
// and that is CORRECT classification, not a misfire (he gets almost no spam,
// so most of his mail genuinely is worth-knowing). So the fix belongs in the
// VIEW, not in the rules: show the top slice by whatever sort is active and
// keep the rest one click away.
const LANE_CAP = 25;

const CATEGORY = {
  customer: { label: "Customer", bg: "rgba(47,93,80,.16)", fg: "#2b6a58" },
  vendor: { label: "Vendor", bg: "rgba(140,109,70,.16)", fg: "#8C6D46" },
  internal: { label: "Internal", bg: "rgba(62,76,89,.14)", fg: "#3E4C59" },
  notification: { label: "Notification", bg: "#ece7df", fg: "#5f6e74" },
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
  // Suggestions returned by a correction. Surfaced immediately, while the
  // user still remembers the thread that triggered them — a suggestion found
  // three days later in a settings tab has lost its context.
  const [suggested, setSuggested] = useState([]);
  const [taught, setTaught] = useState(() => {
    try { return !!localStorage.getItem(TAUGHT_KEY); } catch { return true; }
  });
  // Lanes the user has chosen to see in full. Session-only on purpose: the
  // point is a tidy default every visit, not a remembered preference.
  const [expanded, setExpanded] = useState(() => new Set());
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

  // Intentionally unreferenced while the digest control is a dead label (see
  // the toolbar). Kept wired so restoring the switch is a one-line change the
  // day Mail.Send + the Container Apps Job land.
  // eslint-disable-next-line no-unused-vars
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
        <p className="text-[15px] mb-5" style={{ color: "#343e41" }}>
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

  const lanes = data?.lanes || { needsReply: [], fyi: [], cleanup: [], dismissed: [], handled: [] };
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

  // The correction. Optimistically relocate the row, then record it — the
  // server keeps a snapshot of the signals behind the wrong verdict, which
  // is what makes the correction reusable instead of a one-off nudge.
  const moveThread = async (id, state, why) => {
    const laneKey = Object.keys(STATE_OF_LANE).find((k) => STATE_OF_LANE[k] === state);
    setData((d) => {
      if (!d) return d;
      const next = { ...d, lanes: { ...d.lanes } };
      let moved = null;
      for (const k of ["needsReply", "fyi", "cleanup", "handled", "dismissed"]) {
        const before = next.lanes[k] || [];
        next.lanes[k] = before.filter((t) => {
          if (t.id === id) {
            moved = { ...t, state, dismissed: false, reason: "you moved this here", modelUsed: false };
            return false;
          }
          return true;
        });
      }
      if (moved && laneKey) next.lanes[laneKey] = [moved, ...(next.lanes[laneKey] || [])];
      return next;
    });
    if (!taught) {
      setTaught(true);
      try { localStorage.setItem(TAUGHT_KEY, "1"); } catch { /* ignore */ }
    }
    try {
      const res = await api.reclassifyEmailThread(id, state, why);
      if (res?.suggestions?.length) setSuggested(res.suggestions);
    } catch {
      runSync(false); // recover from server truth
    }
  };

  const acceptSuggestion = async (sid) => {
    setSuggested((s) => s.filter((x) => x.id !== sid));
    try {
      await api.acceptTriageSuggestion(sid);
      setData(await api.emailOverview());
    } catch { /* the Rules tab is the recovery path */ }
  };
  const rejectSuggestion = async (sid) => {
    setSuggested((s) => s.filter((x) => x.id !== sid));
    try { await api.rejectTriageSuggestion(sid); } catch { /* non-critical */ }
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
    { key: "handled", label: "Handled", n: lanes.handled?.length || 0 },
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

  // Capping is skipped while searching: a search IS the user narrowing things
  // down, so hiding results behind a "show all" would fight what they asked
  // for. Because the cap runs AFTER `cmp`, "top 25" means top 25 by the
  // active sort — most important on the default, newest if they switched.
  const capOf = (key, rows) =>
    (q || expanded.has(key) || rows.length <= LANE_CAP) ? rows : rows.slice(0, LANE_CAP);
  const toggleExpanded = (key) => setExpanded((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
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
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "#5f6e74" }} />
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
          <span className="font-mono text-xs shrink-0" style={{ color: "#5f6e74" }}>Updated {fmtTime(data.lastSync)}</span>
        )}
        {error && <span className="text-xs" style={{ color: TIDE }}>{error}</span>}

        {/* Digest: a DEAD LABEL, not a control, until Mail.Send is granted and
            the Container Apps Job exists. A switch that stores a preference
            and then sends nothing for a week is a trust cost on the feature we
            most need people to trust — so there is nothing to flip yet.
            Restore the switch (toggleDigest + user_prefs.digest_enabled are
            both still wired) the day sending goes live. */}
        <span className="flex items-center gap-2 ml-auto text-xs" style={{ color: "#6f7d82" }}>
          Daily digest email
          <span className="font-mono text-[10px] uppercase px-1.5 py-0.5 rounded"
            style={{ background: MIST, color: "#5f6e74" }}>coming soon</span>
        </span>
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
                style={{ color: filter === f.key ? "rgba(255,255,255,.75)" : "#5f6e74" }}>{f.n}</span>
            </button>
          ))}
        </div>
        {/* Rules sits apart from the lane chips on purpose: it is not another
            slice of the inbox, it is where the user tells the triage how their
            own mail works. */}
        <button onClick={() => setFilter(filter === "rules" ? "all" : "rules")}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded border whitespace-nowrap"
          style={filter === "rules"
            ? { background: INK, color: "white", borderColor: INK }
            : { background: "white", color: INK, borderColor: BORDER }}>
          <SlidersHorizontal size={13} /> Triage rules
        </button>
        <div className="flex items-center gap-1" hidden={filter === "rules"}>
          <span className="font-mono text-[10px] uppercase tracking-widest" style={{ color: "#5f6e74" }}>Sort</span>
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
      <div className="text-xs mb-4 flex items-center gap-3 flex-wrap" style={{ color: "#5f6e74" }}
        hidden={filter === "rules"}>
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
        <div className="rounded-lg border p-4 mb-4 text-sm" style={{ borderColor: BORDER, background: "white", color: "#343e41" }}>
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

      {/* Shown once, until the user's first correction. */}
      {!taught && filter !== "rules" && !firstScan && (
        <div className="rounded-lg border p-3 mb-3 flex items-start gap-2 text-sm"
          style={{ borderColor: BORDER, background: "white", color: "#343e41" }}>
          <CornerUpRight size={15} className="mt-0.5 shrink-0" style={{ color: SEA }} />
          <div className="flex-1">
            <span className="font-medium" style={{ color: INK }}>Something in the wrong lane?</span>{" "}
            Hit <span className="font-mono text-xs" style={{ color: SEA }}>Move</span> on the row and
            pick where it belongs. It learns from that — after a few corrections it offers you a
            rule to make it permanent.
          </div>
          <button onClick={() => {
            setTaught(true);
            try { localStorage.setItem(TAUGHT_KEY, "1"); } catch { /* ignore */ }
          }} className="p-2 rounded hover:bg-stone-100 shrink-0" style={{ color: "#5f6e74" }}
            title="Got it">
            <X size={14} />
          </button>
        </div>
      )}

      {/* A correction that just unlocked a rule. Shown inline, at the top of
          the list, because the user still has the thread in mind right now —
          the same card found later in a settings panel has lost its context. */}
      {suggested.length > 0 && filter !== "rules" && (
        <div className="rounded-lg border p-3 mb-3" style={{ borderColor: SEA, background: "rgba(146,37,37,.05)" }}>
          <div className="flex items-center gap-1.5 mb-2">
            <Lightbulb size={14} style={{ color: SEA }} />
            <span className="font-mono text-[10px] font-bold uppercase tracking-widest" style={{ color: SEA }}>
              Want a rule for that?
            </span>
          </div>
          {suggested.map((s) => (
            <div key={s.id} className="mb-2 last:mb-0">
              <div className="text-sm font-medium" style={{ color: INK }}>{s.describe}</div>
              <div className="text-xs mb-1.5" style={{ color: "#5f6e74" }}>{s.rationale}</div>
              <div className="flex items-center gap-2">
                <button onClick={() => acceptSuggestion(s.id)}
                  className="flex items-center gap-1.5 px-3 py-1 rounded text-white text-xs font-medium"
                  style={{ background: INK }}>
                  <Check size={12} /> Add rule
                </button>
                <button onClick={() => rejectSuggestion(s.id)}
                  className="px-3 py-1 rounded text-xs font-medium border bg-white"
                  style={{ borderColor: BORDER, color: "#343e41" }}>
                  Not quite
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Content: rules panel, multi-lane view for "All", or a flat list */}
      {filter === "rules" ? (
        <EmailRules onChanged={async () => {
          try { setData(await api.emailOverview()); } catch { /* keep current view */ }
        }} />
      ) : filter === "all" ? (
        <div className="space-y-3">
          {visibleLanes.map((lane) => {
            const isCollapsed = collapsed.has(lane.key);
            return (
              <section key={lane.key} className="bg-white rounded-lg border overflow-hidden" style={{ borderColor: BORDER }}>
                <button onClick={() => toggleLane(lane.key)}
                  className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-stone-50"
                  style={{ borderBottom: isCollapsed ? "none" : "1px solid " + ROW_LINE }}>
                  {isCollapsed ? <ChevronRight size={15} style={{ color: "#5f6e74" }} /> : <ChevronDown size={15} style={{ color: "#5f6e74" }} />}
                  <span className="font-mono text-[10px] font-bold px-2 py-0.5 rounded-full whitespace-nowrap"
                    style={{ background: lane.bg, color: lane.fg }}>{lane.label}</span>
                  <span className="font-mono text-xs tabular-nums" style={{ color: "#5f6e74" }}>{lane.rows.length}</span>
                </button>

                {!isCollapsed && (
                  <>
                    {lane.note && lane.rows.length > 0 && (
                      <div className="px-3 pt-2 text-[11px]" style={{ color: "#6f7d82" }}>{lane.note}</div>
                    )}
                    {lane.rows.length === 0 ? (
                      <div className="px-3 py-6 text-sm text-center" style={{ color: "#6f7d82" }}>
                        {q ? "No matches in this lane." : lane.empty}
                      </div>
                    ) : (
                      <>
                        <ul>
                          {capOf(lane.key, lane.rows).map((t) => {
                            const canBulk = lane.key === "needsReply" || lane.key === "fyi";
                            return (
                              <ThreadRow
                                key={t.id}
                                t={t}
                                onDismiss={lane.key !== "dismissed" && lane.key !== "cleanup" ? () => dismiss(t.id) : null}
                                onUndismiss={lane.key === "dismissed" ? () => undismiss(t.id) : null}
                                checked={selected.has(t.id)}
                                onToggleCheck={canBulk ? () => toggleSelected(t.id) : null}
                                onMove={(state, why) => moveThread(t.id, state, why)}
                              />
                            );
                          })}
                        </ul>
                        <LaneMore laneKey={lane.key} rows={lane.rows}
                          expanded={expanded.has(lane.key)} searching={!!q}
                          onToggle={() => toggleExpanded(lane.key)} />
                      </>
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
                <div className="px-3 py-2 text-[11px]" style={{ color: "#6f7d82", borderBottom: "1px solid " + ROW_LINE }}>
                  {active.note}
                </div>
              )}
              {flatRows.length === 0 ? (
                <div className="px-3 py-8 text-sm text-center" style={{ color: "#6f7d82" }}>
                  {q ? `No ${active?.label?.toLowerCase() || ""} threads match "${query}".` : active?.empty}
                </div>
              ) : (
                <>
                  <ul>
                    {capOf(filter, flatRows).map((t) => {
                      const canBulk = filter === "needsReply" || filter === "fyi";
                      return (
                        <ThreadRow
                          key={t.id}
                          t={t}
                          onDismiss={filter !== "dismissed" && filter !== "cleanup" ? () => dismiss(t.id) : null}
                          onUndismiss={filter === "dismissed" ? () => undismiss(t.id) : null}
                          checked={selected.has(t.id)}
                          onToggleCheck={canBulk ? () => toggleSelected(t.id) : null}
                          onMove={(state, why) => moveThread(t.id, state, why)}
                        />
                      );
                    })}
                  </ul>
                  <LaneMore laneKey={filter} rows={flatRows}
                    expanded={expanded.has(filter)} searching={!!q}
                    onToggle={() => toggleExpanded(filter)} />
                </>
              )}
            </section>
          );
        })()
      )}
    </div>
  );
}

// The footer under a capped lane. Names the sort in the label so "top 25"
// never looks arbitrary — the user should know WHICH 25 they are looking at.
function LaneMore({ rows, expanded, searching, onToggle }) {
  if (searching || rows.length <= LANE_CAP) return null;
  return (
    <button onClick={onToggle}
      className="w-full px-3 py-2 text-xs font-medium text-left hover:bg-stone-50"
      style={{ borderTop: "1px solid " + ROW_LINE, color: SEA }}>
      {expanded
        ? `Show fewer — back to the top ${LANE_CAP}`
        : `Show all ${rows.length} — ${rows.length - LANE_CAP} more below the top ${LANE_CAP}`}
    </button>
  );
}

function ThreadRow({ t, onDismiss, onUndismiss, checked, onToggleCheck, onMove }) {
  const cat = CATEGORY[t.category] || CATEGORY.other;
  const [menu, setMenu] = useState(false);
  const [why, setWhy] = useState("");
  // Empty webLink used to render as href="#" which reloaded the SPA and
  // looked like "the CRM tab". Fall back to a plain div in that case so
  // there is no phantom click target.
  const hasLink = !!t.webLink;
  const Row = hasLink ? "a" : "div";
  // `_blank` — a NEW tab per row, deliberately. A named target was tried
  // 2026-09-02 and REVERTED (Ethan): the OWA deeplink renders that ONE message
  // with no inbox around it, so reusing a single tab destroys the one you were
  // reading. Stacking lets you open several, come back to Starbot, and decide
  // what to do next. Do not "fix" this into a shared tab.
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
                <ArrowRight size={11} style={{ color: "#5f6e74" }} title="You started this thread" />
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
                <span className="font-mono text-[10px]" style={{ color: "#6f7d82" }}>{t.msgCount} msgs</span>
              )}
            </div>
            <div className="text-sm mt-0.5 break-words" style={{ color: "#343e41", maxWidth: "44rem" }}>
              {t.subject}
            </div>
            {t.snippet && (
              <div className="text-xs mt-0.5 truncate" style={{ color: "#5f6e74", maxWidth: "44rem" }}>
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
              <span className="font-mono text-[10px] px-1.5 py-0.5 rounded" style={{ background: MIST, color: "#55646a" }}>{t.folder}</span>
            )}
            <span className="font-mono text-[11px] tabular-nums" style={{ color: "#5f6e74" }}>{ageOf(t.lastAt)}</span>
            {hasLink && <ExternalLink size={12} style={{ color: "#6f7d82" }} />}
            {onMove && (
              // Labelled, not a bare icon. A correction nobody can find
              // produces no data, and this control IS the feedback loop —
              // discoverability is the feature here, not decoration.
              <button onClick={(e) => { stop(e); setMenu((v) => !v); }}
                className="flex items-center gap-1 px-2.5 py-1.5 rounded text-[10px] font-mono uppercase hover:bg-stone-200"
                title="Wrong lane? Move it — the triage learns from this"
                style={{ color: menu ? SEA : "#5f6e74" }}>
                <CornerUpRight size={11} /> Move
              </button>
            )}
            {onDismiss && (
              <button onClick={(e) => { stop(e); onDismiss(); }}
                className="p-2 rounded hover:bg-stone-200"
                title="Not a task — hide this thread"
                style={{ color: "#5f6e74" }}>
                <X size={13} />
              </button>
            )}
            {onUndismiss && (
              <button onClick={(e) => { stop(e); onUndismiss(); }}
                className="flex items-center gap-1 px-2.5 py-1.5 rounded text-[10px] font-mono uppercase hover:bg-stone-200"
                title="Restore to its lane"
                style={{ color: SEA }}>
                <Undo2 size={11} /> Restore
              </button>
            )}
          </div>
        </div>

        {/* The correction panel. Lives inside the row so the thread it refers
            to is still on screen while the user picks. The optional "why" is
            one tap and it decides whether a resulting rule should generalize
            to the sender at all — "already handled" is about this thread,
            "not my area" is about every thread from them. */}
        {menu && onMove && (
          <div onClick={stop} className="mt-2 rounded border p-2"
            style={{ borderColor: BORDER, background: MIST }}>
            <div className="font-mono text-[10px] uppercase tracking-widest mb-1.5" style={{ color: "#5f6e74" }}>
              Where does this belong?
            </div>
            <div className="flex flex-wrap gap-1 mb-2">
              {MOVE_TARGETS.filter((m) => m.state !== t.state).map((m) => (
                <button key={m.state}
                  onClick={(e) => { stop(e); setMenu(false); onMove(m.state, why || undefined); setWhy(""); }}
                  className="px-2.5 py-1 rounded text-xs font-medium bg-white border hover:bg-stone-100"
                  style={{ borderColor: BORDER, color: INK }}>
                  {m.label}
                </button>
              ))}
            </div>
            <div className="font-mono text-[10px] uppercase tracking-widest mb-1" style={{ color: "#6f7d82" }}>
              Why? (optional)
            </div>
            <div className="flex flex-wrap gap-1">
              {WHY_CHIPS.map((c) => (
                <button key={c.k} onClick={(e) => { stop(e); setWhy(why === c.k ? "" : c.k); }}
                  className="px-2 py-0.5 rounded-full text-[11px] border"
                  style={why === c.k
                    ? { background: SEA, color: "white", borderColor: SEA }
                    : { background: "white", color: "#343e41", borderColor: BORDER }}>
                  {c.label}
                </button>
              ))}
            </div>
          </div>
        )}
      </Row>
    </li>
  );
}
