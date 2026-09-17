// Chat preferences panel — where the user teaches starbot how THEY work.
//
// Two sections, ordered by agency:
//
//   1. Your preferences   Free-text guidance lines the user writes ("I prefer
//                         bullets", "Always check my calendar first") that ride
//                         into the system prompt. CRUD with category tags.
//   2. System prompt      The company-wide identity, capabilities, and rules
//                         that shape every conversation. Read-only for non-admins
//                         but visible so users can see what starbot knows and
//                         request changes from IT.
import { useEffect, useState, useRef } from "react";
import {
  Plus, Trash2, Check, X, Lock, ChevronDown, ChevronRight,
  Sparkles, BookOpen, Upload, FileText, Download,
} from "lucide-react";
import * as api from "./api.js";

const INK = "#1C1C1C";
const MIST = "#F3F0EC";
const SEA = "#922525";
const TIDE = "#C0392B";
const BORDER = "#cdd6d4";
const ROW_LINE = "#e4dfd3";

const CATEGORIES = [
  { k: "general", label: "General" },
  { k: "format", label: "Formatting" },
  { k: "tool", label: "Tool usage" },
  { k: "knowledge", label: "Knowledge" },
];

export default function ChatRules() {
  const [prefs, setPrefs] = useState(null);
  const [prompt, setPrompt] = useState(null);
  const [files, setFiles] = useState(null);
  const [blobReady, setBlobReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [showAdd, setShowAdd] = useState(false);

  const loadPrefs = async () => {
    try {
      setPrefs(await api.listChatPreferences());
    } catch {
      setError("Couldn't load your preferences.");
    }
  };

  const loadPrompt = async () => {
    try {
      setPrompt(await api.getChatSystemPrompt());
    } catch { /* non-critical */ }
  };

  const loadFiles = async () => {
    try {
      setFiles(await api.listChatFiles());
    } catch { /* non-critical */ }
  };

  const loadBlobStatus = async () => {
    try {
      const s = await api.chatFilesStatus();
      setBlobReady(s.configured);
    } catch { /* non-critical */ }
  };

  useEffect(() => { loadPrefs(); loadPrompt(); loadFiles(); loadBlobStatus(); }, []);

  const flash = (msg) => {
    setNote(msg);
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

  if (prefs === null && !error) {
    return (
      <div className="py-12 text-center font-mono text-sm tracking-widest uppercase" style={{ color: SEA }}>
        Loading preferences...
      </div>
    );
  }

  const activeCount = (prefs || []).filter((p) => p.active).length;

  return (
    <div className="space-y-3 overflow-y-auto px-1" style={{ maxHeight: "calc(100vh - 18rem)" }}>
      {/* Header stats */}
      <div className="bg-white rounded-lg border px-3 py-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs"
        style={{ borderColor: BORDER, color: "#5f6e74" }}>
        <span><strong style={{ color: INK }}>{activeCount}</strong> active preference{activeCount === 1 ? "" : "s"}</span>
        <span><strong style={{ color: INK }}>{(prefs || []).length}</strong> total</span>
        {note && <span className="ml-auto" style={{ color: "#2b6a58" }}>{note}</span>}
        {error && <span className="ml-auto" style={{ color: TIDE }}>{error}</span>}
      </div>

      {/* 1. YOUR PREFERENCES ------------------------------------------------- */}
      <section className="bg-white rounded-lg border overflow-hidden" style={{ borderColor: BORDER }}>
        <div className="px-3 py-2.5 flex items-center gap-2" style={{ borderBottom: "1px solid " + ROW_LINE }}>
          <Sparkles size={14} style={{ color: SEA }} />
          <span className="font-mono text-[10px] font-bold uppercase tracking-widest" style={{ color: INK }}>
            Your preferences
          </span>
          <span className="font-mono text-xs tabular-nums" style={{ color: "#5f6e74" }}>{(prefs || []).length}</span>
          <button onClick={() => setShowAdd((v) => !v)}
            className="ml-auto flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium"
            style={{ background: showAdd ? MIST : INK, color: showAdd ? INK : "white" }}>
            <Plus size={12} /> {showAdd ? "Cancel" : "Add"}
          </button>
        </div>

        {showAdd && (
          <AddPrefForm busy={busy}
            onCancel={() => setShowAdd(false)}
            onSubmit={(payload) => guard(async () => {
              await api.createChatPreference(payload);
              await loadPrefs();
              flash("Preference added. It takes effect on your next message.");
              setShowAdd(false);
            })} />
        )}

        {(!prefs || prefs.length === 0) ? (
          <div className="px-3 py-6 text-sm text-center" style={{ color: "#6f7d82" }}>
            No preferences yet. Add one above to shape how starbot responds to you.
            <br />
            <span className="text-xs mt-1 block" style={{ color: "#5f6e74" }}>
              Examples: "I prefer bullet points over paragraphs" or "Always check my calendar before suggesting times"
            </span>
          </div>
        ) : (
          <ul>
            {prefs.map((p) => (
              <li key={p.id} className="px-3 py-2.5 flex items-start gap-3" style={{ borderBottom: "1px solid " + ROW_LINE }}>
                <div className="flex-1 min-w-0">
                  <div className="text-sm break-words" style={{ color: p.active ? INK : "#6f7d82" }}>
                    {p.text}
                  </div>
                  <div className="flex items-center gap-2 mt-0.5 font-mono text-[10px]" style={{ color: "#6f7d82" }}>
                    <span className="px-1.5 py-0.5 rounded" style={{ background: MIST }}>
                      {CATEGORIES.find((c) => c.k === p.category)?.label || p.category}
                    </span>
                    {p.source === "suggested" && <span>suggested</span>}
                  </div>
                </div>
                <button disabled={busy}
                  onClick={() => guard(async () => {
                    await api.updateChatPreference(p.id, { text: p.text, category: p.category, active: !p.active });
                    await loadPrefs();
                    flash(p.active ? "Preference paused." : "Preference active.");
                  })}
                  className="shrink-0 relative w-9 h-5 rounded-full transition-colors disabled:opacity-60"
                  role="switch" aria-checked={p.active}
                  title={p.active ? "Pause this preference" : "Activate this preference"}
                  style={{ background: p.active ? "#2F5D50" : "#cdd6d4" }}>
                  <span className="absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all"
                    style={{ left: p.active ? "18px" : "2px" }} />
                </button>
                <button disabled={busy}
                  onClick={() => guard(async () => {
                    await api.deleteChatPreference(p.id);
                    await loadPrefs();
                    flash("Preference removed.");
                  })}
                  className="shrink-0 p-2 rounded hover:bg-stone-200 disabled:opacity-60"
                  title="Delete this preference" style={{ color: "#5f6e74" }}>
                  <Trash2 size={13} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 2. YOUR FILES ---------------------------------------------------------- */}
      <FilesSection files={files} blobReady={blobReady} busy={busy}
        onUpload={(file) => guard(async () => {
          await api.uploadChatFile(file);
          await loadFiles();
          flash("File uploaded. Starbot can now reference it.");
        })}
        onDelete={(id) => guard(async () => {
          await api.deleteChatFile(id);
          await loadFiles();
          flash("File removed.");
        })} />

      {/* 3. SYSTEM PROMPT (read-only, collapsible) ----------------------------- */}
      {prompt && <SystemPromptView sections={prompt.sections} isAdmin={prompt.isAdmin} />}

    </div>
  );
}

// --- Add preference form -------------------------------------------------------

function AddPrefForm({ busy, onCancel, onSubmit }) {
  const [text, setText] = useState("");
  const [category, setCategory] = useState("general");

  const canSave = text.trim().length > 3;

  const submit = (e) => {
    e.preventDefault();
    if (!canSave) return;
    onSubmit({ text: text.trim(), category });
  };

  return (
    <form onSubmit={submit} className="px-3 py-3" style={{ borderBottom: "1px solid " + ROW_LINE, background: MIST }}>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} maxLength={500}
        placeholder="e.g. I prefer bullet points over long paragraphs when summarizing email."
        className="w-full bg-white border rounded px-2 py-1.5 text-sm" style={{ borderColor: BORDER }} />
      <div className="flex items-center gap-2 mt-2">
        <select value={category} onChange={(e) => setCategory(e.target.value)}
          className="bg-white border rounded px-2 py-1.5 text-xs" style={{ borderColor: BORDER }}>
          {CATEGORIES.map((c) => <option key={c.k} value={c.k}>{c.label}</option>)}
        </select>
        <button type="submit" disabled={busy || !canSave}
          className="px-3 py-1.5 rounded text-white text-xs font-medium disabled:opacity-50"
          style={{ background: INK }}>Save</button>
        <button type="button" onClick={onCancel}
          className="px-3 py-1.5 rounded text-xs font-medium border"
          style={{ borderColor: BORDER, color: "#343e41" }}>Cancel</button>
      </div>
      <div className="text-[11px] mt-1.5" style={{ color: "#5f6e74" }}>
        Written in plain language and added to every conversation you have with starbot.
        Takes effect on your next message.
      </div>
    </form>
  );
}

// --- Files section -------------------------------------------------------------

function FilesSection({ files, blobReady, busy, onUpload, onDelete }) {
  const inputRef = useRef(null);

  const handlePick = (e) => {
    const f = e.target.files?.[0];
    if (f) onUpload(f);
    e.target.value = "";
  };

  const handleDownload = async (id) => {
    try {
      const { url } = await api.getChatFileDownload(id);
      window.open(url, "_blank");
    } catch { /* best-effort */ }
  };

  const fileCount = (files || []).length;

  return (
    <section className="bg-white rounded-lg border overflow-hidden" style={{ borderColor: BORDER }}>
      <div className="px-3 py-2.5 flex items-center gap-2" style={{ borderBottom: "1px solid " + ROW_LINE }}>
        <FileText size={14} style={{ color: SEA }} />
        <span className="font-mono text-[10px] font-bold uppercase tracking-widest" style={{ color: INK }}>
          Your files
        </span>
        <span className="font-mono text-xs tabular-nums" style={{ color: "#5f6e74" }}>{fileCount}</span>
        <input ref={inputRef} type="file" className="hidden" onChange={handlePick}
          accept=".pdf,.doc,.docx,.xls,.xlsx,.pptx,.csv,.txt,.md,.jpg,.jpeg,.png,.webp" />
        <button onClick={() => inputRef.current?.click()} disabled={busy || !blobReady}
          className="ml-auto flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium disabled:opacity-50"
          style={{ background: INK, color: "white" }}
          title={blobReady ? "Upload a file for starbot to reference" : "File uploads not configured yet"}>
          <Upload size={12} /> Upload
        </button>
      </div>

      {!blobReady && (
        <div className="px-3 py-4 text-xs text-center" style={{ color: "#6f7d82" }}>
          File uploads will be available once Azure Blob Storage is connected.
        </div>
      )}

      {blobReady && fileCount === 0 && (
        <div className="px-3 py-6 text-sm text-center" style={{ color: "#6f7d82" }}>
          No files uploaded yet.
          <br />
          <span className="text-xs mt-1 block" style={{ color: "#5f6e74" }}>
            Upload PDFs, spreadsheets, images, or documents to give starbot context about your work.
          </span>
        </div>
      )}

      {fileCount > 0 && (() => {
        const nameCounts = {};
        for (const f of files) nameCounts[f.filename] = (nameCounts[f.filename] || 0) + 1;
        return (
        <ul>
          {files.map((f) => (
            <li key={f.id} className="px-3 py-2.5 flex items-start gap-3" style={{ borderBottom: "1px solid " + ROW_LINE }}>
              <FileText size={14} className="shrink-0 mt-0.5" style={{ color: "#5f6e74" }} />
              <div className="flex-1 min-w-0">
                <div className="text-sm break-words flex items-center gap-2" style={{ color: INK }}>
                  {f.filename}
                  {nameCounts[f.filename] > 1 && (
                    <span className="text-[10px] font-medium px-1.5 py-0.5 rounded" style={{ background: "#fde8e8", color: "#c0392b" }}>
                      Duplicate
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2 mt-0.5 font-mono text-[10px]" style={{ color: "#6f7d82" }}>
                  <span>{f.summary}</span>
                  <span>{f.uploadedAt ? new Date(f.uploadedAt).toLocaleDateString() : ""}</span>
                </div>
              </div>
              <button disabled={busy} onClick={() => handleDownload(f.id)}
                className="shrink-0 p-2 rounded hover:bg-stone-200 disabled:opacity-60"
                title="Download" style={{ color: "#5f6e74" }}>
                <Download size={13} />
              </button>
              <button disabled={busy} onClick={() => onDelete(f.id)}
                className="shrink-0 p-2 rounded hover:bg-stone-200 disabled:opacity-60"
                title="Delete this file" style={{ color: "#5f6e74" }}>
                <Trash2 size={13} />
              </button>
            </li>
          ))}
        </ul>
        );
      })()}
    </section>
  );
}

// --- System prompt (read-only) -------------------------------------------------

function SystemPromptView({ sections, isAdmin }) {
  const [open, setOpen] = useState(false);
  const activeSections = sections.filter((s) => s.active);

  return (
    <section className="bg-white rounded-lg border overflow-hidden" style={{ borderColor: BORDER }}>
      <button onClick={() => setOpen((v) => !v)}
        className="w-full px-3 py-2.5 flex items-center gap-2 text-left hover:bg-stone-50">
        <BookOpen size={14} style={{ color: SEA }} />
        <span className="font-mono text-[10px] font-bold uppercase tracking-widest" style={{ color: INK }}>
          System prompt
        </span>
        <span className="font-mono text-xs tabular-nums" style={{ color: "#5f6e74" }}>
          {activeSections.length} section{activeSections.length === 1 ? "" : "s"}
        </span>
        {!isAdmin && <Lock size={11} style={{ color: "#6f7d82" }} title="Admin-only editing" />}
        <span className="ml-auto font-mono text-[10px]" style={{ color: "#5f6e74" }}>
          {open ? "Hide" : "Show"}
        </span>
      </button>

      {open && (
        <div style={{ borderTop: "1px solid " + ROW_LINE }}>
          <div className="px-3 pt-2 pb-1">
            <p className="text-[11px]" style={{ color: "#5f6e74" }}>
              {isAdmin
                ? "You can edit these sections via the admin API. This is what shapes every starbot conversation."
                : "These are the company-wide instructions that shape every starbot conversation. Only admins can edit them. If something should change, let Ethan know."}
            </p>
          </div>
          {sections.map((s) => (
            <PromptSection key={s.key} section={s} />
          ))}
        </div>
      )}
    </section>
  );
}

function PromptSection({ section }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div style={{ borderTop: "1px solid " + ROW_LINE }}>
      <button onClick={() => setExpanded((v) => !v)}
        className="w-full px-3 py-2 flex items-center gap-2 text-left hover:bg-stone-50">
        {expanded
          ? <ChevronDown size={12} style={{ color: "#5f6e74" }} />
          : <ChevronRight size={12} style={{ color: "#5f6e74" }} />}
        <span className="text-sm font-medium" style={{ color: section.active ? INK : "#6f7d82" }}>
          {section.label || section.key}
        </span>
        {!section.active && (
          <span className="font-mono text-[10px] px-1.5 py-0.5 rounded" style={{ background: "#FBEAE8", color: TIDE }}>
            off
          </span>
        )}
      </button>
      {expanded && (
        <div className="px-3 pb-3 pl-7">
          <pre className="text-xs whitespace-pre-wrap break-words p-2 rounded"
            style={{ background: MIST, color: INK, fontFamily: "ui-monospace, SFMono-Regular, monospace", lineHeight: 1.5 }}>
            {section.content}
          </pre>
        </div>
      )}
    </div>
  );
}

