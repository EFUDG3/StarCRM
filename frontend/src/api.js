// API client for the Star CRM backend.
// Defaults to same-origin ("") so the deployed single Cloud Run service serves
// both the site and the API. In local dev, the Vite proxy (vite.config.js)
// forwards /api to the backend on :8000, so relative paths work there too.
// Override with VITE_API_URL only if you host the API on a different origin.
const BASE = import.meta.env.VITE_API_URL || "";

// The active user is selected client-side (no auth) and remembered locally.
const USER_KEY = "star_crm_user_id";
export const getCurrentUser = () => localStorage.getItem(USER_KEY);
export const setCurrentUser = (id) => {
  if (id) localStorage.setItem(USER_KEY, id);
  else localStorage.removeItem(USER_KEY);
};

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const uid = getCurrentUser();
  if (uid) headers["X-User-Id"] = uid; // scopes contact routes to this profile
  const res = await fetch(`${BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${text || res.statusText}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

// --- Users (profiles) ---
export const listUsers = () => request("/api/users");
export const createUser = (name) =>
  request("/api/users", { method: "POST", body: JSON.stringify({ name }) });
export const renameUser = (id, name) =>
  request(`/api/users/${id}`, { method: "PATCH", body: JSON.stringify({ name }) });
export const deleteUser = (id) =>
  request(`/api/users/${id}`, { method: "DELETE" });

export const listContacts = () => request("/api/contacts");

export const createContact = (data) =>
  request("/api/contacts", { method: "POST", body: JSON.stringify(data) });

export const updateContact = (id, data) =>
  request(`/api/contacts/${id}`, { method: "PUT", body: JSON.stringify(data) });

export const deleteContact = (id) =>
  request(`/api/contacts/${id}`, { method: "DELETE" });

export const logTouch = (id, note) =>
  request(`/api/contacts/${id}/log`, {
    method: "POST",
    body: JSON.stringify({ note }),
  });

export const completeAction = (id) =>
  request(`/api/contacts/${id}/complete`, { method: "POST" });

export const resetData = () => request("/api/reset", { method: "POST" });

// Upload a business-card photo; returns { name, company, role, email, phone,
// cardImage } where cardImage is a compact JPEG data URL to save with the contact.
export const scanCard = async (file, timeoutMs = 30000) => {
  const fd = new FormData();
  // Accepts a picked File or a Blob captured from the webcam canvas (no .name).
  fd.append("file", file, file.name || "card.jpg");
  const uid = getCurrentUser();
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    // No Content-Type header — the browser sets the multipart boundary itself.
    const res = await fetch(`${BASE}/api/contacts/scan-card`, {
      method: "POST",
      headers: uid ? { "X-User-Id": uid } : {},
      body: fd,
      signal: ctrl.signal,
    });
    if (!res.ok) {
      let detail = "";
      try { detail = (await res.json())?.detail || ""; } catch { /* ignore */ }
      const err = new Error(detail || `API ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return await res.json();
  } catch (e) {
    if (e.name === "AbortError") {
      const err = new Error("timeout");
      err.status = 0;
      throw err;
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
};

// --- Starbot (M365 session cookie, not X-User-Id) ---------------------------
// These endpoints authenticate with the httpOnly session cookie set by the
// Microsoft sign-in redirect, so every call needs credentials: "include".
const sessionRequest = async (path, options = {}) => {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json())?.detail || ""; } catch { /* ignore */ }
    const err = new Error(detail || `API ${res.status}`);
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
};

// The session probe is the same for every tab, so cache it: dedupe the calls
// App/Chat/Tasks/Mileage each fire on mount (one request instead of four) and
// let a tab switch reuse the resolved value with no "Checking sign-in…" flash.
// A full page reload (sign-out) resets this module, so the cache never goes
// stale within a session. A failed probe clears the cache so it can retry.
let _mePromise = null;
let _meValue;
export const getCachedMe = () => _meValue;
export const authMe = () => {
  if (!_mePromise) {
    _mePromise = sessionRequest("/api/auth/me")
      .then((v) => { _meValue = v; return v; })
      .catch((e) => { _mePromise = null; throw e; });
  }
  return _mePromise;
};
export const authLogout = () => sessionRequest("/api/auth/logout", { method: "POST" });
// Sign-in is a full-page redirect (Microsoft login), not an XHR.
export const authLoginUrl = () => `${BASE}/api/auth/login`;

// --- Mileage tab -------------------------------------------------------------
export const mileageRoute = (addresses) =>
  sessionRequest("/api/mileage/route", { method: "POST", body: JSON.stringify({ addresses }) });
export const listTrips = () => sessionRequest("/api/mileage/trips");
export const saveTrip = (trip) =>
  sessionRequest("/api/mileage/trips", { method: "POST", body: JSON.stringify(trip) });
export const deleteTrip = (id) =>
  sessionRequest(`/api/mileage/trips/${id}`, { method: "DELETE" });
export const listPlaces = () => sessionRequest("/api/mileage/places");
export const addPlace = (address, label = "") =>
  sessionRequest("/api/mileage/places", { method: "POST", body: JSON.stringify({ address, label }) });
export const deletePlace = (id) =>
  sessionRequest(`/api/mileage/places/${id}`, { method: "DELETE" });
export const listVisits = () => sessionRequest("/api/mileage/visits");
export const deleteVisit = (id) =>
  sessionRequest(`/api/mileage/visits/${id}`, { method: "DELETE" });
export const scanCalendar = (start, end) =>
  sessionRequest("/api/mileage/scan", { method: "POST", body: JSON.stringify({ start, end }) });
export const reportPreview = (start, end) =>
  sessionRequest("/api/mileage/report/preview", { method: "POST", body: JSON.stringify({ start, end }) });
// Returns a file, not JSON — hence not sessionRequest.
export const reportDownload = async (start, end, dates) => {
  const res = await fetch(`${BASE}/api/mileage/report/generate`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ start, end, dates }),
  });
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json())?.detail || ""; } catch { /* ignore */ }
    const err = new Error(detail || `API ${res.status}`);
    err.status = res.status;
    throw err;
  }
  const dispo = res.headers.get("Content-Disposition") || "";
  const m = dispo.match(/filename="?([^";]+)/);
  return { blob: await res.blob(), filename: m ? m[1] : "Mileage Report.xlsx" };
};

// --- Accounts (shared 'hit list' — company-wide, session-cookie auth) --------
export const listAccounts = () => sessionRequest("/api/accounts");
export const createAccount = (data) =>
  sessionRequest("/api/accounts", { method: "POST", body: JSON.stringify(data) });
export const updateAccount = (id, data) =>
  sessionRequest(`/api/accounts/${id}`, { method: "PUT", body: JSON.stringify(data) });
export const deleteAccount = (id) =>
  sessionRequest(`/api/accounts/${id}`, { method: "DELETE" });
export const logAccountNote = (id, note) =>
  sessionRequest(`/api/accounts/${id}/log`, { method: "POST", body: JSON.stringify({ note }) });

// --- Tasks tab aggregator (meetings + flagged replies + CRM follow-ups) ------
export const taskAgenda = () => sessionRequest("/api/tasks/agenda");
export const dismissReply = (id) =>
  sessionRequest("/api/tasks/dismiss", { method: "POST", body: JSON.stringify({ id }) });
export const completeFollowup = (contactId) =>
  sessionRequest(`/api/tasks/followup/${contactId}/complete`, { method: "POST" });

export const listTodos = (includeDone = false) =>
  sessionRequest(`/api/todos${includeDone ? "?include_done=true" : ""}`);
export const addTodo = (text, due = "", priority = "") =>
  sessionRequest("/api/todos", { method: "POST", body: JSON.stringify({ text, due, priority }) });
// Partial update: any of { text, due, status, priority, done }.
export const updateTodo = (id, patch) =>
  sessionRequest(`/api/todos/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
export const setTodoDone = (id, done) => updateTodo(id, { done });
export const deleteTodo = (id) =>
  sessionRequest(`/api/todos/${id}`, { method: "DELETE" });

// One starbot chat turn over SSE. `messages` is the opaque history array the
// previous turn's `done` event returned (plus the new user message — the
// caller appends it). Calls `onEvent` for each parsed event:
//   {type:"text", text} {type:"tool", name} {type:"todos_changed"}
//   {type:"done", messages} {type:"error", message}
export const chatStream = async (messages, onEvent, signal) => {
  const res = await fetch(`${BASE}/api/chat`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
    signal,
  });
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json())?.detail || ""; } catch { /* ignore */ }
    const err = new Error(detail || `API ${res.status}`);
    err.status = res.status;
    throw err;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop(); // last piece may be incomplete
    for (const part of parts) {
      const line = part.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try { onEvent(JSON.parse(line.slice(6))); } catch { /* skip bad frame */ }
    }
  }
};

// --- Projects (shared PM board — company-wide, session-cookie auth) ----------
export const listProjects = () => sessionRequest("/api/projects");
export const createProject = (data) =>
  sessionRequest("/api/projects", { method: "POST", body: JSON.stringify(data) });
export const updateProject = (id, data) =>
  sessionRequest(`/api/projects/${id}`, { method: "PUT", body: JSON.stringify(data) });
export const deleteProject = (id) =>
  sessionRequest(`/api/projects/${id}`, { method: "DELETE" });
export const logProjectNote = (id, note) =>
  sessionRequest(`/api/projects/${id}/log`, { method: "POST", body: JSON.stringify({ note }) });

// --- Email triage (per-user inbox ranking — session-cookie auth) -------------
export const emailOverview = () => sessionRequest("/api/email/overview");
export const emailSync = () =>
  sessionRequest("/api/email/sync", { method: "POST" });
export const setEmailPrefs = (data) =>
  sessionRequest("/api/email/prefs", { method: "PUT", body: JSON.stringify(data) });
