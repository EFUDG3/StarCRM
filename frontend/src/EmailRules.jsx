// Rules panel for Star Mail — where the triage becomes THEIRS.
//
// Four sections, ordered by how much attention each deserves:
//
//   1. Suggestions   Rules the bot proposes after 3+ corrections share a
//                    pattern. Approval required, always. This is the visible
//                    intelligence of the feedback loop, so it goes first.
//   2. Your rules    What the user has told it, in plain language, with a hit
//                    count so a rule that never fires can be deleted.
//   3. About my job  One paragraph that rides into the model prompt. For the
//                    judgment path this outperforms a stack of rules.
//   4. Company rules Every built-in rule, listed even when locked, because a
//                    rule you cannot see is a rule you cannot trust.
//
// Every save re-runs the deterministic pass server-side and returns fresh
// counts, so the user watches their rule take effect instead of being told
// it will apply to future mail.
import { useEffect, useState } from "react";
import {
  Sparkles, Plus, Trash2, Check, X, Lightbulb, Lock, RotateCcw,
} from "lucide-react";
import * as api from "./api.js";

const INK = "#1C1C1C";
const MIST = "#F3F0EC";
const SEA = "#922525";
const TIDE = "#C0392B";
const BORDER = "#cdd6d4";
const ROW_LINE = "#e4dfd3";

const LANE_LABEL = {
  needs_reply: "Needs your reply",
  fyi: "Worth knowing",
  resolved: "Handled",
  bulk: "Cleanup",
};

const SCOPES = [
  { k: "sender", label: "From this address", hint: "marc@example.com" },
  { k: "domain", label: "From anyone at", hint: "datanet.com" },
  { k: "subject", label: "Subject contains", hint: "invoice" },
  { k: "signal", label: "Threads where…", hint: "" },
];

const TIER_LABEL = {
  "-1": "Always on",
  1: "Standard",
  2: "Optional",
};

export default function EmailRules({ onChanged }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [showAdd, setShowAdd] = useState(false);

  const load = async () => {
    try {
      setData(await api.getTriageRules());
    } catch {
      setError("Couldn't load your rules.");
    }
  };
  useEffect(() => { load(); }, []);

  // Every mutation returns retriage stats. Surfacing "N threads moved" is the
  // whole point: a rule you cannot see working is a rule you will not trust.
  const afterWrite = async (res) => {
    const moved = res?.retriage?.changed ?? 0;
    setNote(moved > 0
      ? `${moved} thread${moved === 1 ? "" : "s"} re-sorted.`
      : "Saved. No stored threads matched yet.");
    await load();
    onChanged?.();
    setTimeout(() => setNote(""), 4000);
  };

  const guard = async (fn) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try { await fn(); }
    catch (e) { setError(e?.message || "That didn't save."); }
    finally { setBusy(false); }
  };

  if (!data && !error) {
    return <div className="py-12 text-center font-mono text-sm tracking-widest uppercase" style={{ color: SEA }}>Loading rules…</div>;
  }
  if (!data) {
    return <div className="py-12 text-center text-sm" style={{ color: TIDE }}>{error}</div>;
  }

  const { rules = [], companyRules = [], suggestions = [], correctionCount = 0,
          suggestThreshold = 3, signals = {} } = data;
  const activeCompany = companyRules.filter((r) => r.active).length;

  return (
    <div className="space-y-3">
      {/* Header line: what the loop has learned so far, as a number. */}
      <div className="bg-white rounded-lg border px-3 py-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs"
        style={{ borderColor: BORDER, color: "#5f6e74" }}>
        <span><strong style={{ color: INK }}>{rules.length}</strong> of your rules</span>
        <span><strong style={{ color: INK }}>{activeCompany}</strong> built-in rules on</span>
        <span><strong style={{ color: INK }}>{correctionCount}</strong> correction{correctionCount === 1 ? "" : "s"} recorded</span>
        {note && <span className="ml-auto" style={{ color: "#2b6a58" }}>{note}</span>}
        {error && <span className="ml-auto" style={{ color: TIDE }}>{error}</span>}
      </div>

      {/* 1. SUGGESTIONS ---------------------------------------------------- */}
      <section className="bg-white rounded-lg border overflow-hidden" style={{ borderColor: BORDER }}>
        <div className="px-3 py-2.5 flex items-center gap-2" style={{ borderBottom: "1px solid " + ROW_LINE }}>
          <Lightbulb size={14} style={{ color: SEA }} />
          <span className="font-mono text-[10px] font-bold uppercase tracking-widest" style={{ color: SEA }}>
            Suggested rules
          </span>
          <span className="font-mono text-xs tabular-nums" style={{ color: "#5f6e74" }}>{suggestions.length}</span>
        </div>
        {suggestions.length === 0 ? (
          <div className="px-3 py-6 text-sm text-center" style={{ color: "#6f7d82" }}>
            Nothing to suggest yet. Move {suggestThreshold} or more similar threads to the
            lane they belong in and a rule shows up here for you to approve.
          </div>
        ) : (
          <ul>
            {suggestions.map((s) => (
              <li key={s.id} className="px-3 py-3" style={{ borderBottom: "1px solid " + ROW_LINE }}>
                <div className="text-sm font-medium mb-0.5" style={{ color: INK }}>{s.describe}</div>
                <div className="text-xs mb-2" style={{ color: "#5f6e74" }}>{s.rationale}</div>
                <div className="flex items-center gap-2">
                  <button disabled={busy}
                    onClick={() => guard(async () => afterWrite(await api.acceptTriageSuggestion(s.id)))}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded text-white text-xs font-medium disabled:opacity-60"
                    style={{ background: INK }}>
                    <Check size={12} /> Add rule
                  </button>
                  <button disabled={busy}
                    onClick={() => guard(async () => { await api.rejectTriageSuggestion(s.id); await load(); })}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium border disabled:opacity-60"
                    style={{ borderColor: BORDER, color: "#343e41" }}>
                    <X size={12} /> Not quite
                  </button>
                  <span className="font-mono text-[10px] ml-auto" style={{ color: "#6f7d82" }}>
                    {s.evidenceCount} correction{s.evidenceCount === 1 ? "" : "s"}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 2. YOUR RULES ----------------------------------------------------- */}
      <section className="bg-white rounded-lg border overflow-hidden" style={{ borderColor: BORDER }}>
        <div className="px-3 py-2.5 flex items-center gap-2" style={{ borderBottom: "1px solid " + ROW_LINE }}>
          <span className="font-mono text-[10px] font-bold uppercase tracking-widest" style={{ color: INK }}>
            Your rules
          </span>
          <span className="font-mono text-xs tabular-nums" style={{ color: "#5f6e74" }}>{rules.length}</span>
          <button onClick={() => setShowAdd((v) => !v)}
            className="ml-auto flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium"
            style={{ background: showAdd ? MIST : INK, color: showAdd ? INK : "white" }}>
            <Plus size={12} /> {showAdd ? "Cancel" : "Add a rule"}
          </button>
        </div>

        {showAdd && (
          <AddRuleForm signals={signals} busy={busy}
            onCancel={() => setShowAdd(false)}
            onSubmit={(payload) => guard(async () => {
              afterWrite(await api.createTriageRule(payload));
              setShowAdd(false);
            })} />
        )}

        {rules.length === 0 ? (
          <div className="px-3 py-6 text-sm text-center" style={{ color: "#6f7d82" }}>
            No rules of your own yet. Add one above, or let the bot suggest them from
            how you sort your mail.
          </div>
        ) : (
          <ul>
            {rules.map((r) => (
              <li key={r.id} className="px-3 py-2.5 flex items-start gap-3" style={{ borderBottom: "1px solid " + ROW_LINE }}>
                <div className="flex-1 min-w-0">
                  <div className="text-sm break-words" style={{ color: r.active ? INK : "#6f7d82" }}>
                    {r.describe}
                  </div>
                  <div className="flex items-center gap-2 mt-0.5 font-mono text-[10px]" style={{ color: "#6f7d82" }}>
                    {r.kind === "soft" && (
                      <span className="inline-flex items-center gap-1" style={{ color: SEA }}>
                        <Sparkles size={9} /> judgment
                      </span>
                    )}
                    {r.source === "suggested" && <span>suggested</span>}
                    <span>fired {r.hitCount}x</span>
                    {r.hitCount === 0 && r.kind === "hard" && (
                      <span style={{ color: TIDE }}>never matched</span>
                    )}
                  </div>
                </div>
                <button disabled={busy}
                  onClick={() => guard(async () => afterWrite(await api.updateTriageRule(r.id, { active: !r.active })))}
                  className="shrink-0 relative w-9 h-5 rounded-full transition-colors disabled:opacity-60"
                  role="switch" aria-checked={r.active}
                  title={r.active ? "Turn this rule off" : "Turn this rule on"}
                  style={{ background: r.active ? "#2F5D50" : "#cdd6d4" }}>
                  <span className="absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all"
                    style={{ left: r.active ? "18px" : "2px" }} />
                </button>
                <button disabled={busy}
                  onClick={() => guard(async () => afterWrite(await api.deleteTriageRule(r.id)))}
                  className="shrink-0 p-2 rounded hover:bg-stone-200 disabled:opacity-60"
                  title="Delete this rule" style={{ color: "#5f6e74" }}>
                  <Trash2 size={13} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 3. ABOUT MY JOB --------------------------------------------------- */}
      <ProfileBox initial={data.profile || ""} busy={busy}
        onSave={(profile) => guard(async () => afterWrite(await api.setTriageProfile({ profile })))} />

      {/* 4. MAINTENANCE ----------------------------------------------------- */}
      <Maintenance />

      {/* 5. COMPANY RULES -------------------------------------------------- */}
      <CompanyRules rules={companyRules} busy={busy}
        onToggle={(id, on) => guard(async () => {
          const next = companyRules
            .filter((r) => r.editable && (r.id === id ? on : r.active))
            .map((r) => r.id);
          afterWrite(await api.setTriageProfile({ companyRules: next }));
        })} />
    </div>
  );
}

// --- Add a rule ---------------------------------------------------------------

function AddRuleForm({ signals, busy, onCancel, onSubmit }) {
  const [kind, setKind] = useState("hard");
  const [scope, setScope] = useState("sender");
  const [pattern, setPattern] = useState("");
  const [target, setTarget] = useState("fyi");
  const [text, setText] = useState("");

  const scopeDef = SCOPES.find((s) => s.k === scope);
  const canSave = kind === "soft" ? text.trim().length > 3 : pattern.trim().length > 0;

  const submit = (e) => {
    e.preventDefault();
    if (!canSave) return;
    onSubmit(kind === "soft"
      ? { kind: "soft", text: text.trim() }
      : { kind: "hard", scope, pattern: pattern.trim(), action: "force_state", targetState: target });
  };

  return (
    <form onSubmit={submit} className="px-3 py-3" style={{ borderBottom: "1px solid " + ROW_LINE, background: MIST }}>
      {/* Hard vs soft is a real distinction, so it is named in the user's
          terms: an exact match runs instantly and for free, a judgment rule
          needs the model and only applies where the rules are unsure. */}
      <div className="flex gap-0.5 p-0.5 rounded bg-white w-fit mb-3" style={{ border: "1px solid " + BORDER }}>
        {[
          { k: "hard", label: "Exact match" },
          { k: "soft", label: "Judgment call" },
        ].map((o) => (
          <button key={o.k} type="button" onClick={() => setKind(o.k)}
            className="px-3 py-1 text-xs font-medium rounded"
            style={kind === o.k ? { background: INK, color: "white" } : { background: "white", color: INK }}>
            {o.label}
          </button>
        ))}
      </div>

      {kind === "hard" ? (
        <div className="flex flex-wrap items-center gap-2">
          <select value={scope} onChange={(e) => { setScope(e.target.value); setPattern(""); }}
            className="bg-white border rounded px-2 py-1.5 text-sm" style={{ borderColor: BORDER }}>
            {SCOPES.map((s) => <option key={s.k} value={s.k}>{s.label}</option>)}
          </select>

          {scope === "signal" ? (
            <select value={pattern} onChange={(e) => setPattern(e.target.value)}
              className="bg-white border rounded px-2 py-1.5 text-sm flex-1 min-w-56" style={{ borderColor: BORDER }}>
              <option value="">Pick a condition…</option>
              {Object.entries(signals).map(([k, label]) => (
                <option key={k} value={k}>{label}</option>
              ))}
            </select>
          ) : (
            <input value={pattern} onChange={(e) => setPattern(e.target.value)}
              placeholder={scopeDef?.hint} autoComplete="off"
              className="bg-white border rounded px-2 py-1.5 text-sm flex-1 min-w-48" style={{ borderColor: BORDER }} />
          )}

          <span className="text-sm" style={{ color: "#5f6e74" }}>goes to</span>
          <select value={target} onChange={(e) => setTarget(e.target.value)}
            className="bg-white border rounded px-2 py-1.5 text-sm" style={{ borderColor: BORDER }}>
            {Object.entries(LANE_LABEL).map(([k, label]) => (
              <option key={k} value={k}>{label}</option>
            ))}
          </select>
        </div>
      ) : (
        <div>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} maxLength={500}
            placeholder="e.g. I don't need to reply to LeighAnn unless she asks me something directly."
            className="w-full bg-white border rounded px-2 py-1.5 text-sm" style={{ borderColor: BORDER }} />
          <div className="text-[11px] mt-1" style={{ color: "#5f6e74" }}>
            Written in plain language and handed to the triage model. Applies to threads
            the exact-match rules can't settle on their own.
          </div>
        </div>
      )}

      <div className="flex items-center gap-2 mt-3">
        <button type="submit" disabled={busy || !canSave}
          className="px-3 py-1.5 rounded text-white text-xs font-medium disabled:opacity-50"
          style={{ background: INK }}>Save rule</button>
        <button type="button" onClick={onCancel}
          className="px-3 py-1.5 rounded text-xs font-medium border"
          style={{ borderColor: BORDER, color: "#343e41" }}>Cancel</button>
      </div>
    </form>
  );
}

// --- About my job -------------------------------------------------------------

function ProfileBox({ initial, busy, onSave }) {
  const [text, setText] = useState(initial);
  const dirty = text.trim() !== (initial || "").trim();

  return (
    <section className="bg-white rounded-lg border overflow-hidden" style={{ borderColor: BORDER }}>
      <div className="px-3 py-2.5 flex items-center gap-2" style={{ borderBottom: "1px solid " + ROW_LINE }}>
        <span className="font-mono text-[10px] font-bold uppercase tracking-widest" style={{ color: INK }}>
          About your job
        </span>
      </div>
      <div className="px-3 py-3">
        <p className="text-xs mb-2" style={{ color: "#5f6e74" }}>
          A sentence or two about what you actually do. The triage reads this before it
          judges anything, and for most people it changes more verdicts than any single rule.
        </p>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={2000}
          placeholder="e.g. I run IT for Star. DataNet support tickets are my real work, not noise. I don't handle sales leads."
          className="w-full bg-white border rounded px-2 py-1.5 text-sm" style={{ borderColor: BORDER }} />
        <div className="flex items-center gap-2 mt-2">
          <button disabled={busy || !dirty} onClick={() => onSave(text)}
            className="px-3 py-1.5 rounded text-white text-xs font-medium disabled:opacity-50"
            style={{ background: INK }}>Save</button>
          {dirty && (
            <button onClick={() => setText(initial)}
              className="flex items-center gap-1 px-2 py-1.5 rounded text-xs border"
              style={{ borderColor: BORDER, color: "#343e41" }}>
              <RotateCcw size={11} /> Revert
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

// --- Maintenance --------------------------------------------------------------

// Rebuilding from Outlook is rare and costs real time, so it lives here rather
// than beside Refresh, where it would get clicked by mistake. It exists because
// some fixes change how a message is PARSED (snippets are built at sync time),
// which no amount of re-triaging can reach.
function Maintenance() {
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState("");
  const [error, setError] = useState("");

  const rebuild = async () => {
    if (running) return;
    setRunning(true);
    setError("");
    setDone("");
    try {
      const out = await api.resyncEmail();
      const s = out?.syncStats || {};
      setDone(`Re-read ${s.changed ?? 0} messages across ${s.threads ?? 0} threads.`);
    } catch (e) {
      setError(e?.message || "The rebuild didn't finish. Try again in a moment.");
    } finally {
      setRunning(false);
    }
  };

  return (
    <section className="bg-white rounded-lg border overflow-hidden" style={{ borderColor: BORDER }}>
      <div className="px-3 py-2.5" style={{ borderBottom: "1px solid " + ROW_LINE }}>
        <span className="font-mono text-[10px] font-bold uppercase tracking-widest" style={{ color: INK }}>
          Maintenance
        </span>
      </div>
      <div className="px-3 py-3">
        <div className="text-sm font-medium" style={{ color: INK }}>Rebuild from Outlook</div>
        <p className="text-xs mt-0.5 mb-2" style={{ color: "#5f6e74" }}>
          Re-reads your last 30 days from scratch and re-sorts everything. Use this after an
          update changes how mail is read. Takes about a minute — a normal Refresh is much
          faster and is what you want day to day. Threads you moved by hand stay where you put them.
        </p>
        <div className="flex items-center gap-2">
          <button onClick={rebuild} disabled={running}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium border disabled:opacity-60"
            style={{ borderColor: BORDER, color: INK, background: MIST }}>
            <RotateCcw size={12} className={running ? "animate-spin" : ""} />
            {running ? "Rebuilding — about a minute…" : "Rebuild from Outlook"}
          </button>
          {done && <span className="text-xs" style={{ color: "#2b6a58" }}>{done}</span>}
          {error && <span className="text-xs" style={{ color: TIDE }}>{error}</span>}
        </div>
      </div>
    </section>
  );
}

// --- Company rules ------------------------------------------------------------

function CompanyRules({ rules, busy, onToggle }) {
  const [open, setOpen] = useState(false);
  const groups = [
    { tier: -1, label: "Always on", note: "Facts about the thread, not preferences. These can't be switched off." },
    { tier: 1, label: "Standard", note: "Mechanical filters everyone starts with. Turn one off if it gets in your way." },
    { tier: 2, label: "Optional", note: "Judgment rules. Off unless you turned them on or accepted a suggestion." },
  ];

  return (
    <section className="bg-white rounded-lg border overflow-hidden" style={{ borderColor: BORDER }}>
      <button onClick={() => setOpen((v) => !v)}
        className="w-full px-3 py-2.5 flex items-center gap-2 text-left hover:bg-stone-50">
        <span className="font-mono text-[10px] font-bold uppercase tracking-widest" style={{ color: INK }}>
          Built-in rules
        </span>
        <span className="font-mono text-xs tabular-nums" style={{ color: "#5f6e74" }}>
          {rules.filter((r) => r.active).length}/{rules.length} on
        </span>
        <span className="ml-auto font-mono text-[10px]" style={{ color: "#5f6e74" }}>
          {open ? "Hide" : "Show"}
        </span>
      </button>

      {open && groups.map((g) => {
        const rows = rules.filter((r) => r.tier === g.tier);
        if (rows.length === 0) return null;
        return (
          <div key={g.tier} style={{ borderTop: "1px solid " + ROW_LINE }}>
            <div className="px-3 pt-2.5 pb-1">
              <div className="font-mono text-[10px] font-bold uppercase tracking-widest" style={{ color: SEA }}>
                {g.label}
              </div>
              <div className="text-[11px] mt-0.5" style={{ color: "#6f7d82" }}>{g.note}</div>
            </div>
            <ul>
              {rows.map((r) => (
                <li key={r.id} className="px-3 py-2 flex items-start gap-3" style={{ borderTop: "1px solid " + ROW_LINE }}>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm break-words" style={{ color: r.active ? INK : "#6f7d82" }}>{r.label}</div>
                    <div className="text-[11px] mt-0.5" style={{ color: "#5f6e74" }}>{r.why}</div>
                  </div>
                  {r.editable ? (
                    <button disabled={busy} onClick={() => onToggle(r.id, !r.active)}
                      className="shrink-0 relative w-9 h-5 rounded-full transition-colors disabled:opacity-60"
                      role="switch" aria-checked={r.active}
                      style={{ background: r.active ? "#2F5D50" : "#cdd6d4" }}>
                      <span className="absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all"
                        style={{ left: r.active ? "18px" : "2px" }} />
                    </button>
                  ) : (
                    <Lock size={13} className="shrink-0 mt-0.5" style={{ color: "#6f7d82" }} title="Always on" />
                  )}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </section>
  );
}
