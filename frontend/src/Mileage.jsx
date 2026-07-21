// Mileage tab: the v2 standalone tracker rebuilt inside starbot.
// Route math runs server-side on Azure Maps; trips and places persist per
// user in the DB (the old page lost its log on every refresh). The Places
// rail is a self-building address book — filled by saved trips and by
// scanning the user's own Outlook calendar for street addresses. Scanned
// addresses are day-stamped (visits), so the rail shows day groups and a
// whole day's stops load into the trip form in one click.
import { useEffect, useState } from "react";
import {
  Plus, Trash2, Copy, MapPin, Calendar, Check, ChevronDown, ChevronUp,
  Route, ClipboardList,
} from "lucide-react";
import * as api from "./api.js";

const INK = "#1C1C1C";
const MIST = "#F3F0EC";
const SEA = "#922525";
const TIDE = "#C0392B";

const OFFICE = "4610 Alvarado Canyon Rd, San Diego, CA 92120";

const todayISO = () => new Date().toISOString().slice(0, 10);
const daysAgoISO = (n) => {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
};
const fmtMoney = (n) => `$${n.toFixed(2)}`;
// Company rate is $0.7250 — 3 decimals when they matter, 2 when they don't.
const fmtRate = (r) => {
  const s = r.toFixed(3);
  return `$${s.endsWith("0") ? s.slice(0, -1) : s}`;
};

export default function MileageTracker() {
  const [me, setMe] = useState(null);
  const [places, setPlaces] = useState([]);
  const [visits, setVisits] = useState([]); // day-stamped calendar addresses
  const [newIds, setNewIds] = useState(new Set());
  const [expandedIds, setExpandedIds] = useState(new Set()); // log rows showing all addresses
  const [rate, setRate] = useState(null); // null = not loaded; user edits stick
  const [trips, setTrips] = useState(null); // null = loading
  const [subTab, setSubTab] = useState("entry"); // entry | log

  const [startAddr, setStartAddr] = useState(OFFICE);
  const [tripDate, setTripDate] = useState(todayISO()); // travel day, not save day
  const [stops, setStops] = useState([""]);
  const [returnTrip, setReturnTrip] = useState(true);
  const [result, setResult] = useState(null); // /route response + its date
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  const [scanStart, setScanStart] = useState(daysAgoISO(7));
  const [scanEnd, setScanEnd] = useState(todayISO());
  const [scanBusy, setScanBusy] = useState(false);
  const [scanMsg, setScanMsg] = useState("");

  useEffect(() => {
    api.authMe().then(setMe).catch(() => setMe({ signedIn: false, configured: true }));
  }, []);

  const refresh = async () => {
    const [pl, tr, vs] = await Promise.all([
      api.listPlaces().catch(() => []),
      api.listTrips().catch(() => ({ rate: 0.725, trips: [] })),
      api.listVisits().catch(() => []),
    ]);
    setPlaces(pl);
    setRate((cur) => (cur == null ? tr.rate : cur)); // don't clobber a custom rate
    setTrips(tr.trips);
    setVisits(vs);
  };

  useEffect(() => {
    if (me?.signedIn) refresh();
  }, [me?.signedIn]);

  // --- Entry ----------------------------------------------------------------
  const setStop = (i, v) => setStops((cur) => cur.map((s, j) => (j === i ? v : s)));
  const addStopField = () => setStops((cur) => [...cur, ""]);
  const removeStopField = (i) =>
    setStops((cur) => (cur.length > 1 ? cur.filter((_, j) => j !== i) : [""]));

  // Clicking a place fills the first empty stop, or appends a new one.
  const useAsStop = (address) => {
    setStops((cur) => {
      const i = cur.findIndex((s) => !s.trim());
      if (i === -1) return [...cur, address];
      return cur.map((s, j) => (j === i ? address : s));
    });
  };

  const calculate = async () => {
    const mid = stops.map((s) => s.trim()).filter(Boolean);
    if (mid.length === 0) {
      setError("Add at least one stop.");
      return;
    }
    const addresses = [startAddr.trim() || OFFICE, ...mid];
    if (returnTrip) addresses.push(startAddr.trim() || OFFICE);
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      const r = await api.mileageRoute(addresses);
      // The trip carries the TRAVEL day (form field, prefilled by "Load day"
      // or today) — not the day the save button happened to be clicked.
      setResult({ ...r, date: tripDate || todayISO() });
    } catch (e) {
      setError(e.message || "Route failed.");
      setResult(null);
    }
    setBusy(false);
  };

  const saveToLog = async () => {
    if (!result || saved) return;
    try {
      await api.saveTrip({
        date: result.date,
        legs: result.legs,
        totalMiles: result.totalMiles,
        rate: effRate, // the entry-form rate rides with the trip
        resolved: result.resolved,
      });
      setSaved(true);
      refresh();
    } catch (e) {
      setError(e.message || "Could not save trip.");
    }
  };

  const removeTrip = async (t) => {
    await api.deleteTrip(t.id).catch(() => {});
    refresh();
  };

  // --- Places ----------------------------------------------------------------
  const removePlace = async (p) => {
    await api.deletePlace(p.id).catch(() => {});
    refresh();
  };

  // --- Visits (day groups) ----------------------------------------------------
  // visits arrive flat, newest day first; group them into [date, rows] pairs.
  const visitDays = (() => {
    const by = new Map();
    visits.forEach((v) => {
      if (!by.has(v.date)) by.set(v.date, []);
      by.get(v.date).push(v);
    });
    return [...by.entries()].sort(([a], [b]) => b.localeCompare(a));
  })();

  const dayName = (iso) =>
    new Date(iso + "T12:00:00").toLocaleDateString(undefined, { weekday: "short" });

  // One click turns a scanned day into the trip form: its stops, its date.
  const loadDay = (date, dayVisits) => {
    setTripDate(date);
    setStops(dayVisits.map((v) => v.address));
    setResult(null);
    setSaved(false);
    setError("");
  };

  const removeVisit = async (v) => {
    await api.deleteVisit(v.id).catch(() => {});
    refresh();
  };

  const scan = async () => {
    setScanBusy(true);
    setScanMsg("");
    try {
      const r = await api.scanCalendar(scanStart, scanEnd);
      setNewIds(new Set(r.new.map((p) => p.id)));
      const parts = [`${r.scanned} event${r.scanned === 1 ? "" : "s"} scanned`,
                     `${r.new.length} new place${r.new.length === 1 ? "" : "s"}`];
      if (r.visits?.length) parts.push(`${r.visits.length} day-stamped stop${r.visits.length === 1 ? "" : "s"}`);
      if (r.skipped?.length) parts.push(`${r.skipped.length} not mappable`);
      setScanMsg(parts.join(" · "));
      refresh();
    } catch (e) {
      setScanMsg(e.message || "Scan failed.");
    }
    setScanBusy(false);
  };

  // --- Log helpers ------------------------------------------------------------
  // Each saved entry stands alone: no day grouping and NO grand total —
  // back-to-back saves can be different workers at different rates, so any
  // rolled-up number is meaningless. Dollars come from the server, priced at
  // each trip's own stored rate.
  const effRate = rate == null || Number.isNaN(rate) ? 0.725 : rate;

  // The stops actually visited. The return-to-start drive (when present) is
  // not a stop — detected by the last drive ending where the trip began, so
  // trips saved WITHOUT the round-trip box no longer lose their last stop in
  // the summary.
  const stopsOf = (t) => {
    const dests = t.legs.map((l) => l.to);
    if (dests.length > 1 && dests[dests.length - 1] === t.legs[0]?.from) dests.pop();
    return dests;
  };

  const toggleExpanded = (id) =>
    setExpandedIds((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const copyLog = () => {
    let text = "Date\tStops\tMiles\tRate\tAmount\n";
    (trips || []).slice().reverse().forEach((t) => {
      text += `${t.date}\t${stopsOf(t).join("; ")}\t${t.totalMiles.toFixed(1)}\t${t.rate}\t${t.dollars.toFixed(2)}\n`;
    });
    navigator.clipboard.writeText(text).catch(() => {});
  };

  // --- Gates ------------------------------------------------------------------
  if (me === null || (me?.signedIn && trips === null)) {
    return (
      <div className="py-16 text-center font-mono text-sm tracking-widest uppercase" style={{ color: SEA }}>
        Loading mileage…
      </div>
    );
  }

  if (!me.signedIn) {
    return (
      <section className="bg-white rounded-lg p-8 text-center border-l-4 max-w-lg mx-auto mt-8" style={{ borderColor: SEA }}>
        <MapPin size={28} style={{ color: SEA }} className="mx-auto mb-3" />
        <h2 className="text-xl font-bold mb-2" style={{ fontFamily: "Georgia, serif" }}>Mileage tracker</h2>
        <p className="text-[15px] mb-5" style={{ color: "#4a5a60" }}>
          Trips and saved places are tied to your Microsoft account. Sign in to start logging.
        </p>
        <a href={api.authLoginUrl()} className="inline-flex items-center gap-2 px-5 py-2.5 rounded text-white text-sm font-medium" style={{ background: INK }}>
          Sign in with Microsoft
        </a>
      </section>
    );
  }

  const field = "w-full bg-white border rounded px-3 py-2 text-[15px]";
  const bc = { borderColor: "#cdd6d4" };
  const cap = "font-mono text-[10px] uppercase tracking-widest block mb-1";

  return (
    <div className="space-y-4">
      {/* Sub-tabs: trip building and the saved log are separate pages — the
          entry side already carries the places rail + day groups. Styled to
          match the app header's segmented control. */}
      <div className="flex w-fit gap-0.5 p-0.5 rounded bg-white" style={{ border: "1px solid #cdd6d4" }}>
        <button
          onClick={() => setSubTab("entry")}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded whitespace-nowrap"
          style={subTab === "entry" ? { background: INK, color: "white" } : { background: "white", color: INK }}
        >
          <Route size={14} /> Trip entry
        </button>
        <button
          onClick={() => setSubTab("log")}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded whitespace-nowrap"
          style={subTab === "log" ? { background: INK, color: "white" } : { background: "white", color: INK }}
        >
          <ClipboardList size={14} /> Log{(trips || []).length > 0 ? ` (${trips.length})` : ""}
        </button>
      </div>

    <div className={subTab === "entry" ? "grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem] items-start" : ""}>
      {/* shared autocomplete source for every stop input */}
      <datalist id="mileage-places">
        {places.map((p) => (
          <option key={p.id} value={p.address}>{p.label || p.address}</option>
        ))}
      </datalist>

      <div className="min-w-0 space-y-4">
        {subTab === "entry" && (<>
        {/* Entry */}
        <section className="bg-white rounded-lg p-5 border-l-4" style={{ borderColor: SEA }}>
          <div className="font-mono text-xs uppercase tracking-widest mb-4" style={{ color: SEA }}>
            Trip entry
          </div>
          <div className="grid gap-3 sm:grid-cols-2 mb-3">
            <label className="block">
              <span className={cap} style={{ color: SEA }}>Trip date</span>
              <input
                type="date"
                className={field} style={bc}
                value={tripDate}
                onChange={(e) => setTripDate(e.target.value)}
                title="The day this trip was driven — prefilled by 'Load day', saved with the trip."
              />
            </label>
            <label className="block">
              <span className={cap} style={{ color: SEA }}>Rate ($/mile)</span>
              <input
                type="number" step="0.0005" min="0.01" max="5"
                className={field} style={bc}
                value={rate ?? 0.725}
                onChange={(e) => setRate(parseFloat(e.target.value))}
                title="Reimbursement rate for THIS entry — saved with the trip. Default $0.7250 (company rate, per HR's mileage sheet)."
              />
            </label>
            <label className="block sm:col-span-2">
              <span className={cap} style={{ color: SEA }}>Start address</span>
              <input className={field} style={bc} list="mileage-places" value={startAddr} onChange={(e) => setStartAddr(e.target.value)} />
            </label>
          </div>

          <span className={cap} style={{ color: SEA }}>Stops</span>
          <div className="space-y-2 mb-2">
            {stops.map((s, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className="font-mono text-xs w-5 text-right shrink-0" style={{ color: "#8b9a9f" }}>{i + 1}</span>
                <input
                  className={field}
                  style={bc}
                  list="mileage-places"
                  placeholder="Full address (saved places autocomplete)"
                  value={s}
                  onChange={(e) => setStop(i, e.target.value)}
                />
                <button onClick={() => removeStopField(i)} className="p-1.5 rounded hover:bg-stone-100 shrink-0" style={{ color: TIDE }} title="Remove stop">
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
          <button onClick={addStopField} className="flex items-center gap-1 px-2.5 py-1.5 rounded text-xs font-medium mb-3" style={{ background: MIST, color: INK }}>
            <Plus size={13} /> Add stop
          </button>

          <label className="flex items-center gap-2 rounded px-3 py-2 mb-4 cursor-pointer" style={{ background: MIST }}>
            <input type="checkbox" checked={returnTrip} onChange={(e) => setReturnTrip(e.target.checked)} style={{ accentColor: SEA }} />
            <span className="text-[15px]">Return to start (round trip)</span>
          </label>

          <button
            onClick={calculate}
            disabled={busy}
            className="w-full py-2.5 rounded text-white text-[15px] font-medium disabled:opacity-60"
            style={{ background: SEA }}
          >
            {busy ? "Calculating…" : "Calculate mileage"}
          </button>
          {error && (
            <div className="mt-3 rounded p-2.5 text-sm" style={{ background: "#FBEAE8", color: TIDE, border: `1px solid ${TIDE}` }}>
              {error}
            </div>
          )}
        </section>

        {/* Result */}
        {result && (
          <section className="bg-white rounded-lg overflow-hidden border-l-4" style={{ borderColor: "#C8B89A" }}>
            <div className="flex items-center justify-between gap-3 px-4 py-2.5" style={{ background: MIST }}>
              <span className="font-mono text-xs" style={{ color: "#4a5a60" }}>{result.date}</span>
              <span className="flex items-center gap-2">
                <span className="font-mono text-sm font-bold px-2.5 py-1 rounded text-white" style={{ background: SEA }}>
                  {result.totalMiles.toFixed(1)} mi
                </span>
                <span className="font-mono text-sm font-bold" style={{ color: "#2F5D50" }}>
                  {fmtMoney(result.totalMiles * effRate)}
                </span>
              </span>
            </div>
            <table className="w-full text-[13px]">
              <tbody>
                {result.legs.map((l, i) => (
                  <tr key={i} className="border-t" style={{ borderColor: "#eee9e2" }}>
                    <td className="px-4 py-2" style={{ color: "#4a5a60" }}>{i + 1}. {l.from}</td>
                    <td className="px-1 py-2" style={{ color: "#8b9a9f" }}>→</td>
                    <td className="px-2 py-2" style={{ color: "#4a5a60" }}>{l.to}</td>
                    <td className="px-2 py-2 text-right font-mono font-medium whitespace-nowrap">{l.miles.toFixed(1)} mi</td>
                    <td className="px-4 py-2 text-right font-mono whitespace-nowrap" style={{ color: "#8b9a9f" }}>{l.minutes}m</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="px-4 py-3 border-t" style={{ borderColor: "#eee9e2" }}>
              <button
                onClick={saveToLog}
                disabled={saved}
                className="flex items-center gap-1.5 px-4 py-2 rounded text-white text-sm font-medium disabled:opacity-60"
                style={{ background: saved ? "#2F5D50" : INK }}
              >
                {saved ? <><Check size={14} /> Saved to log</> : <><Plus size={14} /> Save to log</>}
              </button>
            </div>
          </section>
        )}
        </>)}

        {/* Log — its own page; the entry side stays focused on building trips */}
        {subTab === "log" && (trips || []).length === 0 && (
          <section className="text-sm text-center py-10 rounded bg-white" style={{ color: "#8b9a9f", border: "1px dashed #e5e0d8" }}>
            No saved trips yet — build one on the Trip entry page and it lands here.
          </section>
        )}
        {subTab === "log" && (trips || []).length > 0 && (
          <section className="bg-white rounded-lg p-5 border-l-4" style={{ borderColor: SEA }}>
            <div className="flex items-center justify-between mb-3">
              <span className="font-mono text-xs uppercase tracking-widest" style={{ color: SEA }}>Mileage log</span>
              <button onClick={copyLog} className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium" style={{ background: MIST, color: INK }} title="Copy as spreadsheet rows">
                <Copy size={13} /> Copy for spreadsheet
              </button>
            </div>
            <div className="space-y-2">
              {(trips || []).map((t) => {
                const open = expandedIds.has(t.id);
                const tripStops = stopsOf(t);
                return (
                  <div key={t.id} className="group rounded px-3 py-2" style={{ border: "1px solid #e5e0d8" }}>
                    <div className="flex items-center justify-between gap-2">
                      <button
                        onClick={() => toggleExpanded(t.id)}
                        className="flex items-baseline gap-3 min-w-0 text-left flex-1"
                        title={open ? "Hide addresses" : "Show all addresses"}
                      >
                        <span className="font-mono text-xs font-bold shrink-0">{t.date}</span>
                        {!open && (
                          <span className="text-[13px] truncate" style={{ color: "#4a5a60" }}>
                            {tripStops.length} stop{tripStops.length === 1 ? "" : "s"}: {tripStops.slice(0, 3).join(" · ")}{tripStops.length > 3 ? " …" : ""}
                          </span>
                        )}
                        {open && (
                          <span className="text-[13px]" style={{ color: "#8b9a9f" }}>
                            {tripStops.length} stop{tripStops.length === 1 ? "" : "s"}
                          </span>
                        )}
                      </button>
                      <span className="flex items-center gap-3 shrink-0">
                        <span className="font-mono text-[13px] font-medium">{t.totalMiles.toFixed(1)} mi</span>
                        <span className="font-mono text-[13px]" style={{ color: "#2F5D50" }}>{fmtMoney(t.dollars)}</span>
                        <span className="font-mono text-[11px]" style={{ color: "#8b9a9f" }}>@ {fmtRate(t.rate)}</span>
                        <button onClick={() => toggleExpanded(t.id)} className="p-1 rounded hover:bg-stone-100" style={{ color: INK }} title={open ? "Collapse" : "Expand addresses"}>
                          {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                        </button>
                        <button onClick={() => removeTrip(t)} className="p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-stone-100" style={{ color: TIDE }} title="Delete entry">
                          <Trash2 size={13} />
                        </button>
                      </span>
                    </div>
                    {open && (
                      <div className="starbot-wrap mt-2 pt-2 space-y-1 border-t" style={{ borderColor: "#eee9e2" }}>
                        <div className="text-[12px]" style={{ color: "#8b9a9f" }}>
                          Start: {t.legs[0]?.from}
                        </div>
                        {t.legs.map((l, i) => (
                          <div key={i} className="flex items-baseline justify-between gap-3 text-[13px]">
                            {/* i past the stop count = the drive back to start */}
                            <span style={{ color: "#4a5a60" }}>{i + 1}. {l.to}{i >= tripStops.length ? " (return)" : ""}</span>
                            <span className="font-mono text-[12px] shrink-0" style={{ color: "#8b9a9f" }}>
                              {l.miles.toFixed(1)} mi · {l.minutes}m
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="font-mono text-[11px] mt-2" style={{ color: "#8b9a9f" }}>
              Each entry keeps the rate it was saved with — set the rate in the trip form before calculating.
            </div>
          </section>
        )}
      </div>

      {/* Places rail */}
      {subTab === "entry" && (
      <aside className="bg-white rounded-lg p-4 border-l-4" style={{ borderColor: "#C8B89A" }}>
        <div className="flex items-center gap-2 mb-3">
          <MapPin size={16} style={{ color: SEA }} />
          <span className="font-mono text-xs uppercase tracking-widest" style={{ color: SEA }}>Places</span>
        </div>

        <div className="rounded p-3 mb-3" style={{ background: MIST }}>
          <div className="flex items-center gap-1.5 mb-2">
            <Calendar size={13} style={{ color: SEA }} />
            <span className="font-mono text-[10px] uppercase tracking-widest" style={{ color: "#4a5a60" }}>
              Pull from my calendar
            </span>
          </div>
          <div className="flex gap-2 mb-2">
            <input type="date" className="flex-1 border rounded px-2 py-1.5 text-xs bg-white min-w-0" style={bc} value={scanStart} onChange={(e) => setScanStart(e.target.value)} />
            <input type="date" className="flex-1 border rounded px-2 py-1.5 text-xs bg-white min-w-0" style={bc} value={scanEnd} onChange={(e) => setScanEnd(e.target.value)} />
          </div>
          <button onClick={scan} disabled={scanBusy} className="w-full py-1.5 rounded text-white text-xs font-medium disabled:opacity-60" style={{ background: INK }}>
            {scanBusy ? "Scanning…" : "Scan events for addresses"}
          </button>
          {scanMsg && <div className="font-mono text-[11px] mt-2" style={{ color: "#4a5a60" }}>{scanMsg}</div>}
        </div>

        {/* Day groups: what the scan day-stamped. "Load day" rebuilds that
            day's route in the trip form — date included. */}
        {visitDays.length > 0 && (
          <div className="mb-3">
            <div className="font-mono text-[10px] uppercase tracking-widest mb-1.5" style={{ color: "#4a5a60" }}>
              Scanned days
            </div>
            <div className="space-y-1.5 max-h-80 overflow-y-auto pr-1">
              {visitDays.map(([date, dv]) => (
                <div key={date} className="rounded overflow-hidden" style={{ border: "1px solid #e5e0d8" }}>
                  <div className="flex items-center justify-between gap-2 px-2 py-1.5" style={{ background: MIST }}>
                    <span className="font-mono text-[11px] font-bold">{dayName(date)} {date}</span>
                    <button
                      onClick={() => loadDay(date, dv)}
                      className="px-2 py-0.5 rounded text-white text-[10px] font-medium shrink-0"
                      style={{ background: SEA }}
                      title="Fill the trip form with this day's stops and date"
                    >
                      Load day ({dv.length})
                    </button>
                  </div>
                  <ul>
                    {dv.map((v) => (
                      <li key={v.id} className="group flex items-start gap-1 px-2 py-1 border-t bg-white" style={{ borderColor: "#eee9e2" }}>
                        <button onClick={() => useAsStop(v.address)} className="starbot-wrap flex-1 min-w-0 text-left" title="Add as next stop">
                          {v.label && <span className="block text-[11px] font-semibold leading-snug">{v.label}</span>}
                          <span className="block text-[11px] leading-snug" style={{ color: "#4a5a60" }}>{v.address}</span>
                        </button>
                        <button onClick={() => removeVisit(v)} className="p-0.5 mt-0.5 rounded opacity-0 group-hover:opacity-100 hover:bg-stone-100 shrink-0" style={{ color: TIDE }} title="Remove this stop from this day">
                          <Trash2 size={11} />
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </div>
        )}

        {places.length > 0 && (
          <div className="font-mono text-[10px] uppercase tracking-widest mb-1.5" style={{ color: "#4a5a60" }}>
            Address book
          </div>
        )}
        <ul className="space-y-1.5 max-h-[30rem] overflow-y-auto pr-1">
          {places.map((p) => (
            <li key={p.id} className="group">
              <div className="flex items-start gap-1.5">
                <button
                  onClick={() => useAsStop(p.address)}
                  className="starbot-wrap flex-1 min-w-0 text-left rounded p-2 hover:shadow-sm"
                  style={{ background: MIST, border: "1px solid #e5e0d8" }}
                  title="Add as next stop"
                >
                  {p.label && <div className="text-[13px] font-semibold leading-snug">{p.label}</div>}
                  <div className="text-[12px] leading-snug" style={{ color: "#4a5a60" }}>{p.address}</div>
                  <div className="font-mono text-[10px] mt-0.5" style={{ color: "#8b9a9f" }}>
                    {newIds.has(p.id) && <span className="font-bold mr-1.5" style={{ color: SEA }}>NEW</span>}
                    {p.timesUsed > 0 && `${p.timesUsed}× used`}
                    {p.timesUsed > 0 && p.lastUsed && " · "}
                    {p.lastUsed && `last ${p.lastUsed}`}
                    {!p.timesUsed && !p.lastUsed && p.source === "calendar" && "from calendar"}
                  </div>
                </button>
                <button onClick={() => removePlace(p)} className="p-1 mt-1 rounded opacity-0 group-hover:opacity-100 hover:bg-stone-100 shrink-0" style={{ color: TIDE }} title="Delete place">
                  <Trash2 size={12} />
                </button>
              </div>
            </li>
          ))}
          {places.length === 0 && (
            <li className="text-xs text-center py-6 rounded" style={{ color: "#8b9a9f", border: "1px dashed #e5e0d8" }}>
              No saved places yet. Scan your calendar above, or save a trip — its stops land here.
            </li>
          )}
        </ul>
        <div className="font-mono text-[10px] mt-3 leading-relaxed" style={{ color: "#8b9a9f" }}>
          Click a place to add it as a stop. Stop fields also autocomplete from this list.
        </div>
      </aside>
      )}
    </div>
    </div>
  );
}
