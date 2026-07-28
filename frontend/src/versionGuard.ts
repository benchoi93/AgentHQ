// Auto-reload guard for stale SPA shells.
//
// A running single-page app never re-fetches its own index.html, so after a
// redeploy an already-open tab keeps executing the OLD hashed JS chunk. That is
// what silently reverts fixes like the web-terminal scroll handler: the server
// is fixed, but the tab is still running last week's bundle. nginx serves
// index.html with `Cache-Control: no-cache` (docker/nginx.conf), so re-fetching
// it always reflects the currently deployed build.
//
// Strategy: record the exact chunk hash this tab is RUNNING (read straight from
// the live <script> tag — no fetch race), then periodically and on focus
// re-fetch index.html and compare. If the deployed hash changed, a newer build
// exists and we reload once to adopt it. Reloading is lossless here: xterm is a
// passive mirror of a server-side tmux PTY, so no local input is buffered.

const CHUNK_RE = /\/assets\/index-([A-Za-z0-9_-]+)\.js/;
const RELOADED_KEY = "ahq_reloaded_for_build";
const CHECK_INTERVAL_MS = 60_000;

// The hash of the chunk this tab actually loaded and is running right now.
function runningHash(): string | null {
  const el = document.querySelector(
    'script[type="module"][src*="/assets/index-"]',
  ) as HTMLScriptElement | null;
  const m = el?.src.match(CHUNK_RE);
  return m ? m[1] : null;
}

// The hash of the chunk the currently deployed index.html points to.
async function deployedHash(): Promise<string | null> {
  try {
    const res = await fetch("/index.html", { cache: "no-store" });
    if (!res.ok) return null;
    const m = (await res.text()).match(CHUNK_RE);
    return m ? m[1] : null;
  } catch {
    return null; // network blip — never reload on uncertainty
  }
}

let bootHash: string | null = null;
let checking = false;

async function checkForUpdate(): Promise<void> {
  if (checking || bootHash === null) return;
  checking = true;
  try {
    const deployed = await deployedHash();
    if (!deployed || deployed === bootHash) return;
    // A newer build is live. Reload once — sessionStorage guards against loops
    // in the unlikely event the reload does not actually adopt the new build.
    if (sessionStorage.getItem(RELOADED_KEY) === deployed) return;
    sessionStorage.setItem(RELOADED_KEY, deployed);
    // eslint-disable-next-line no-console
    console.info(
      `[versionGuard] deployed build ${deployed} != running ${bootHash} — reloading`,
    );
    location.reload();
  } finally {
    checking = false;
  }
}

export function startVersionGuard(): void {
  bootHash = runningHash();
  if (bootHash === null) return; // dev server / unhashed build — nothing to guard
  window.setInterval(() => void checkForUpdate(), CHECK_INTERVAL_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void checkForUpdate();
  });
  window.addEventListener("focus", () => void checkForUpdate());
}
