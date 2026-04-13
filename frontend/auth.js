// Firebase Auth (browser) entrypoint.
// Bundled via esbuild into `app/static/firebase-auth.js`.
import { initializeApp } from "firebase/app";
import { getAnalytics } from "firebase/analytics";
import { getAuth, GoogleAuthProvider, signInWithPopup, signInWithEmailAndPassword, signOut, onAuthStateChanged } from "firebase/auth";

// Your web app's Firebase configuration
const firebaseConfig = {
  apiKey: "AIzaSyDI_iSjY-jJoGCif8YvNHfy7UqOP25Jj3c",
  authDomain: "fnb-pdf-to-excel-prod-491212.firebaseapp.com",
  projectId: "fnb-pdf-to-excel-prod-491212",
  storageBucket: "fnb-pdf-to-excel-prod-491212.firebasestorage.app",
  messagingSenderId: "1052852371581",
  appId: "1:1052852371581:web:437a0658ea3c264033b114",
  measurementId: "G-1CKX5LWWTG"
};

// Initialize Firebase
const app = initializeApp(firebaseConfig);
let analytics = null;
try {
  analytics = getAnalytics(app); // optional in environments where analytics is available
} catch (_err) {
  analytics = null;
}

const auth = getAuth(app);
const googleProvider = new GoogleAuthProvider();
const BILLING_CACHE_KEY_PREFIX = "billingDataCacheV3";
const BILLING_CACHE_TTL_MS = 120000;
let bootstrapInFlight = null;
let homeRevealDone = false;
let authBootstrapEpoch = 0;
let signOutInFlight = false;

function $(id) {
  return document.getElementById(id);
}

function setAuthUiSignedOut() {
  $("signedOutState").style.display = "block";
  $("signedInState").style.display = "none";
  $("previewBtn").disabled = true;
  $("extractStatus").textContent = "";
  if ($("uploadSection")) $("uploadSection").style.display = "none";
  if ($("usageWidget")) $("usageWidget").style.display = "none";
  if ($("requestAccessWrap")) $("requestAccessWrap").style.display = "inline";
  if ($("adminLinkWrap")) $("adminLinkWrap").style.display = "none";
  sessionStorage.removeItem("idToken");
  localStorage.removeItem("idToken");
}

function setAuthUiPending() {
  // Avoid signed-out flash while Firebase restores persisted auth state.
  $("signedOutState").style.display = "none";
  $("signedInState").style.display = "none";
  $("previewBtn").disabled = true;
  if ($("uploadSection")) $("uploadSection").style.display = "none";
  if ($("usageWidget")) $("usageWidget").style.display = "none";
  if ($("adminLinkWrap")) $("adminLinkWrap").style.display = "none";
}

function setAuthUiSignedIn(userEmail) {
  $("signedOutState").style.display = "none";
  $("signedInState").style.display = "block";
  $("userEmail").textContent = userEmail || "user";
  $("previewBtn").disabled = false;
  $("extractStatus").textContent = "";
  if ($("uploadSection")) $("uploadSection").style.display = "block";
  if ($("usageWidget")) $("usageWidget").style.display = "block";
  if ($("requestAccessWrap")) $("requestAccessWrap").style.display = "none";
}

function setStatus(text) {
  const el = $("authStatus");
  if (el) el.textContent = text || "";
}

function startHomeOverlay() {
  document.body.classList.remove("ready");
  document.body.classList.add("bootstrapping");
}

function finishHomeOverlay() {
  if (homeRevealDone) return;
  homeRevealDone = true;
  document.body.classList.remove("bootstrapping");
  document.body.classList.add("ready");
}

function getCookie(name) {
  const target = `${name}=`;
  const parts = document.cookie ? document.cookie.split(";") : [];
  for (const part of parts) {
    const trimmed = part.trim();
    if (trimmed.startsWith(target)) return decodeURIComponent(trimmed.substring(target.length));
  }
  return "";
}

async function apiFetch(url, options, fallbackUser) {
  const opts = Object.assign({ credentials: "same-origin" }, options || {});
  opts.headers = Object.assign({}, (options && options.headers) || {});
  let resp = await fetch(url, opts);
  if ((resp.status === 401 || resp.status === 403) && fallbackUser) {
    // First try to refresh backend session once, then retry cookie path.
    await establishBackendSession(fallbackUser).catch(() => {});
    resp = await fetch(url, opts);
    if (resp.status === 401 || resp.status === 403) {
      // Transitional fallback: keep Bearer support while migrating.
      const idToken = await fallbackUser.getIdToken();
      sessionStorage.setItem("idToken", idToken);
      localStorage.setItem("idToken", idToken);
      const fallbackOptions = Object.assign({}, opts, {
        headers: Object.assign({}, opts.headers, { Authorization: `Bearer ${idToken}` }),
      });
      resp = await fetch(url, fallbackOptions);
    }
  }
  return resp;
}

async function apiFetchWithCsrf(url, options, fallbackUser) {
  const csrfToken = getCookie("csrf_token");
  const opts = Object.assign({}, options || {});
  opts.headers = Object.assign({}, (options && options.headers) || {});
  if (csrfToken) {
    opts.headers["X-CSRF-Token"] = csrfToken;
  }
  return apiFetch(url, opts, fallbackUser);
}

async function establishBackendSession(user) {
  if (signOutInFlight) return;
  if (bootstrapInFlight) return bootstrapInFlight;
  const expectedUid = user?.uid || "";
  bootstrapInFlight = (async () => {
    if (!auth.currentUser || auth.currentUser.uid !== expectedUid) return;
    const idToken = await user.getIdToken();
    const resp = await fetch("/auth/session", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id_token: idToken }),
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(text || `session bootstrap failed (${resp.status})`);
    }
    if (!auth.currentUser || auth.currentUser.uid !== expectedUid) return;
  })();
  try {
    await bootstrapInFlight;
  } finally {
    bootstrapInFlight = null;
  }
}

function billingCacheKeyForUser(userId) {
  return `${BILLING_CACHE_KEY_PREFIX}:${String(userId || "anonymous")}`;
}

function readBillingCache(userId) {
  try {
    const raw = sessionStorage.getItem(billingCacheKeyForUser(userId));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    const ageMs = Date.now() - Number(parsed.cachedAt || 0);
    if (ageMs > BILLING_CACHE_TTL_MS) return null;
    return parsed.payload || null;
  } catch (_err) {
    return null;
  }
}

function writeBillingCache(userId, payload) {
  try {
    sessionStorage.setItem(
      billingCacheKeyForUser(userId),
      JSON.stringify({ cachedAt: Date.now(), payload })
    );
  } catch (_err) {
    // Ignore quota/storage errors.
  }
}

function clearBillingCache(userId) {
  sessionStorage.removeItem(billingCacheKeyForUser(userId));
}

function formatRand(value) {
  return new Intl.NumberFormat("en-ZA", {
    style: "currency",
    currency: "ZAR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(value || 0));
}

async function signInWithGoogle() {
  setStatus("Signing in with Google...");
  await signInWithPopup(auth, googleProvider);
}

async function signInWithEmailPassword() {
  const email = ($("emailInput").value || "").trim();
  const password = $("passwordInput").value || "";
  if (!email || !password) {
    setStatus("Please enter email and password.");
    return;
  }

  setStatus("Signing in...");
  await signInWithEmailAndPassword(auth, email, password);
}

async function doSignOut() {
  setStatus("Signing out...");
  signOutInFlight = true;
  const uid = auth.currentUser?.uid;
  try {
    if (bootstrapInFlight) {
      await bootstrapInFlight.catch(() => {});
    }
    await fetch("/auth/session", { method: "DELETE", credentials: "same-origin" }).catch(() => {});
    await signOut(auth);
    clearBillingCache(uid);
    sessionStorage.removeItem("idToken");
    localStorage.removeItem("idToken");
    setStatus("");
  } finally {
    signOutInFlight = false;
  }
}

async function handleExtractSubmit(event) {
  event.preventDefault();

  const user = auth.currentUser;
  if (!user) {
    setStatus("Please sign in first.");
    return;
  }

  const form = $("extractForm");
  const formData = new FormData(form);
  const enableOcr = $("enable_ocr").checked;
  if (enableOcr) {
    formData.set("enable_ocr", "true");
  } else {
    formData.delete("enable_ocr");
  }

  $("extractStatus").textContent = "Preparing preview...";

  const resp = await apiFetchWithCsrf("/extract/preview", {
    method: "POST",
    body: formData,
  }, user);

  if (!resp.ok) {
    let msg = "Request failed. Please try again.";
    try {
      const body = await resp.json();
      const detail = body && body.detail;
      if (detail && typeof detail === "object" && detail.code === "billing_limit_reached") {
        msg = "You\u2019ve reached your monthly billing limit. You can increase it on the Billing page.";
      } else if (detail && typeof detail === "string") {
        msg = detail;
      } else if (typeof detail === "object" && detail.message) {
        msg = detail.message;
      }
    } catch (_jsonErr) {
      const text = await resp.text().catch(() => "");
      if (text) msg = text;
    }
    $("extractStatus").textContent = msg;
    return;
  }

  const payload = await resp.json();
  if (!payload || !payload.session_id) {
    $("extractStatus").textContent = "Error: preview response missing session id.";
    return;
  }
  clearBillingCache(user.uid);
  window.location.href = `/review?session_id=${encodeURIComponent(payload.session_id)}`;
  setStatus("");
}

async function loadBillingWidget() {
  const user = auth.currentUser;
  if (!user || !$("usageWidget") || !$("usageSummary")) return;
  try {
    const cached = readBillingCache(user.uid);
    if (cached) {
      const cachedRollup = (cached.report && cached.report.rollup) || {};
      const cachedSettings = cached.settings || {};
      const cachedOcr = Number(cachedRollup.total_documents || cachedRollup.total_statements || 0);
      const cachedNonOcr = Number(cachedRollup.total_non_ocr_documents || 0);
      const cachedDocs = cachedOcr + cachedNonOcr;
      const cachedBilled = Number(cachedRollup.total_billable || 0);
      const cachedLimit = Number(cachedSettings.monthly_limit_amount || 0);
      const cachedPct = cachedLimit > 0 ? Math.min(100, Math.round((cachedBilled / cachedLimit) * 100)) : 0;
      $("usageSummary").textContent =
        `Documents: ${cachedDocs} | Billed: ${formatRand(cachedBilled)} | Limit: ${formatRand(cachedLimit)} (${cachedPct}%)`;
      return;
    }

    const resp = await apiFetch("/billing/data", {}, user);
    if (!resp.ok) {
      $("usageSummary").textContent = "Billing summary unavailable.";
      return;
    }
    const payload = await resp.json();
    writeBillingCache(user.uid, payload);
    const rollup = (payload.report && payload.report.rollup) || {};
    const settings = payload.settings || {};
    const ocrDocs = Number(rollup.total_documents || rollup.total_statements || 0);
    const nonOcrDocs = Number(rollup.total_non_ocr_documents || 0);
    const documents = ocrDocs + nonOcrDocs;
    const billed = Number(rollup.total_billable || 0);
    const limit = Number(settings.monthly_limit_amount || 0);
    const pct = limit > 0 ? Math.min(100, Math.round((billed / limit) * 100)) : 0;
    $("usageSummary").textContent =
      `Documents: ${documents} | Billed: ${formatRand(billed)} | Limit: ${formatRand(limit)} (${pct}%)`;
  } catch (_err) {
    $("usageSummary").textContent = "Billing summary unavailable.";
  }
}

async function loadAdminLink() {
  const user = auth.currentUser;
  if (!user || !$("adminLinkWrap")) return;
  try {
    const resp = await apiFetch("/admin/me", {}, user);
    if (!resp.ok) {
      $("adminLinkWrap").style.display = "none";
      return;
    }
    const payload = await resp.json();
    $("adminLinkWrap").style.display = payload && payload.is_admin ? "inline" : "none";
  } catch (_err) {
    $("adminLinkWrap").style.display = "none";
  }
}

// Bind UI when the page is ready
document.addEventListener("DOMContentLoaded", () => {
  startHomeOverlay();
  // Safety guard so overlay never gets stuck on unexpected client errors.
  setTimeout(() => finishHomeOverlay(), 8000);

  // Buttons
  $("googleSignInBtn").addEventListener("click", async () => {
    try {
      await signInWithGoogle();
    } catch (e) {
      setStatus(`Google sign-in failed: ${e?.message || String(e)}`);
    }
  });

  $("emailSignInBtn").addEventListener("click", async () => {
    try {
      await signInWithEmailPassword();
      setStatus("");
    } catch (e) {
      setStatus(`Email sign-in failed: ${e?.message || String(e)}`);
    }
  });

  $("signOutBtn").addEventListener("click", async () => {
    try {
      await doSignOut();
      setAuthUiSignedOut();
    } catch (e) {
      setStatus(`Sign-out failed: ${e?.message || String(e)}`);
    }
  });

  // Form submission
  $("extractForm").addEventListener("submit", (e) => {
    handleExtractSubmit(e).catch((err) => {
      $("extractStatus").textContent = `Error: ${err?.message || String(err)}`;
    });
  });

  // Auth state listener
  setAuthUiPending();
  setStatus("Checking sign-in...");
  onAuthStateChanged(auth, (user) => {
    authBootstrapEpoch += 1;
    const currentEpoch = authBootstrapEpoch;
    if (!user) {
      setAuthUiSignedOut();
      setStatus("");
      if (currentEpoch === authBootstrapEpoch) finishHomeOverlay();
      return;
    }
    // Build the signed-in shell, then reveal page after initial data hydration.
    setAuthUiSignedIn(user.email);
    setStatus("");
    const sessionPromise = establishBackendSession(user).catch(async () => {
      // Transitional fallback: keep Bearer path alive if session bootstrap fails.
      const idToken = await user.getIdToken();
      sessionStorage.setItem("idToken", idToken);
      localStorage.setItem("idToken", idToken);
    });
    Promise.allSettled([sessionPromise]).finally(() => {
      if (currentEpoch !== authBootstrapEpoch) return;
      const billingPromise = loadBillingWidget().catch(() => {});
      const adminPromise = loadAdminLink().catch(() => {});
      Promise.allSettled([billingPromise, adminPromise]).finally(() => {
        if (currentEpoch === authBootstrapEpoch) finishHomeOverlay();
      });
    });
  });
});
