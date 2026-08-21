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
  Building2, CheckCircle2,
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
    key: "waiting", label: "Waiting on them",
    bg: "rgba(62,76,89,.14)", fg: "#3E4C59", open: true,
    empty: "No threads where you spoke last.",
  },
  {
    key: "fyi", label: "Worth knowing",
    bg: "rgba(140,109,70,.16)", fg: "#8C6D46", open: false,
    empty: "Nothing informational right now.",
  },
  {
    key: "cleanup", label: "Cleanup candidates",
    bg: "#ece7df", fg: "#6b7a80", open: false,
    empty: "No bulk mail detected.",
    note: "Newsletters and notifications the filter caught. Reviewing and moving these to trash arrives in a later update.",
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

  const lanes = data?.lanes || { needsReply: [], waiting: [], fyi: [], cleanup: [] };
  const needCount = lanes.needsReply.length;

  return (
    <div>
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3 mb-1">
        <button onClick={() => runSync(false)} disabled={syncing}
          className="flex items-center gap-1.5 px-3 py-2 rounded text-white text-sm font-medium disabled:opacity-60 shrink-0"
          style={{ background: INK }}>
          <RefreshCw size={14} className={syncing ? "animate-spin" : ""} />
          {syncing ? "Scanning…" : "Refresh"}
        </button>
        {data?.lastSync && !syncing && (
          <span className="font-mono text-xs" style={{ color: "#8b9a9f" }}>Updated {fmtTime(data.lastSync)}</span>
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

      <div className="text-xs mb-4 flex items-center gap-2 flex-wrap" style={{ color: "#8b9a9f" }}>
        <span>
          {needCount === 0 ? "Nothing needs your reply" : `${needCount} thread${needCount === 1 ? "" : "s"} need${needCount === 1 ? "s" : ""} your reply`}
          {" · "}{lanes.waiting.length} waiting on them
        </span>
        {(data?.resolvedCount || 0) > 0 && (
          <span className="inline-flex items-center gap-1" style={{ color: "#2b6a58" }}>
            <CheckCircle2 size={12} /> {data.resolvedCount} resolved themselves — not shown
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

      {/* Lanes */}
      <div className="space-y-3">
        {LANES.map((lane) => {
          const rows = lanes[lane.key] || [];
          const isCollapsed = collapsed.has(lane.key);
          return (
            <section key={lane.key} className="bg-white rounded-lg border overflow-hidden" style={{ borderColor: BORDER }}>
              <button onClick={() => toggleLane(lane.key)}
                className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-stone-50"
                style={{ borderBottom: isCollapsed ? "none" : "1px solid " + ROW_LINE }}>
                {isCollapsed ? <ChevronRight size={15} style={{ color: "#8b9a9f" }} /> : <ChevronDown size={15} style={{ color: "#8b9a9f" }} />}
                <span className="font-mono text-[10px] font-bold px-2 py-0.5 rounded-full whitespace-nowrap"
                  style={{ background: lane.bg, color: lane.fg }}>{lane.label}</span>
                <span className="font-mono text-xs tabular-nums" style={{ color: "#8b9a9f" }}>{rows.length}</span>
              </button>

              {!isCollapsed && (
                <>
                  {lane.note && rows.length > 0 && (
                    <div className="px-3 pt-2 text-[11px]" style={{ color: "#b0b8ba" }}>{lane.note}</div>
                  )}
                  {rows.length === 0 ? (
                    <div className="px-3 py-6 text-sm text-center" style={{ color: "#b0b8ba" }}>{lane.empty}</div>
                  ) : (
                    <ul>
                      {rows.map((t) => <ThreadRow key={t.id} t={t} />)}
                    </ul>
                  )}
                </>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}

function ThreadRow({ t }) {
  const cat = CATEGORY[t.category] || CATEGORY.other;
  return (
    <li>
      <a href={t.webLink || "#"} target="_blank" rel="noreferrer"
        className="block px-3 py-2.5 hover:bg-stone-50"
        style={{ borderBottom: "1px solid " + ROW_LINE }}>
        <div className="flex items-start gap-3 flex-wrap">
          <div className="flex-1 min-w-56">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-semibold text-sm break-words" style={{ color: INK }}>
                {t.senderName || t.senderEmail || "Unknown sender"}
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
            <ExternalLink size={12} style={{ color: "#b0b8ba" }} />
          </div>
        </div>
      </a>
    </li>
  );
}
