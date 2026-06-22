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
export const scanCard = async (file) => {
  const fd = new FormData();
  fd.append("file", file);
  const uid = getCurrentUser();
  // No Content-Type header — the browser sets the multipart boundary itself.
  const res = await fetch(`${BASE}/api/contacts/scan-card`, {
    method: "POST",
    headers: uid ? { "X-User-Id": uid } : {},
    body: fd,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${text || res.statusText}`);
  }
  return res.json();
};

// Fetch a stored card image (needs the user header, so we can't use a plain
// <img src>) and return an object URL. Caller should revoke it when done.
export const fetchCardBlobUrl = async (id) => {
  const uid = getCurrentUser();
  const res = await fetch(`${BASE}/api/contacts/${id}/card`, {
    headers: uid ? { "X-User-Id": uid } : {},
  });
  if (!res.ok) return null;
  return URL.createObjectURL(await res.blob());
};
