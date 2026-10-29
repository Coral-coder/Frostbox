/* Shared helpers for Frostbox fridge apps.
 *
 * Inside the kiosk, apps borrow the logged-in Home Assistant connection from
 * the dashboard iframe (same origin). Opened on their own, they fall back to
 * the tokens the HA frontend keeps in localStorage, so open Home Assistant
 * once in this browser first.
 */
(function () {
  "use strict";

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function hassIn(win) {
    try {
      const el = win.document.querySelector("home-assistant");
      if (el && el.hass && el.hass.connection) return el.hass;
      const dash = win.document.getElementById("dash");
      const inner = dash && dash.contentDocument && dash.contentDocument.querySelector("home-assistant");
      if (inner && inner.hass && inner.hass.connection) return inner.hass;
    } catch (e) {
      /* cross-origin or not ready */
    }
    return null;
  }

  function findHass() {
    let win = window;
    for (let depth = 0; depth < 4; depth++) {
      const h = hassIn(win);
      if (h) return h;
      if (win.parent === win) break;
      win = win.parent;
    }
    return null;
  }

  // ---- standalone websocket (only when not inside the kiosk) --------------
  let socket = null;
  let socketReady = null;
  let nextId = 1;
  const pending = new Map();
  const subscribers = new Map();

  async function accessToken() {
    let tokens;
    try {
      tokens = JSON.parse(localStorage.getItem("hassTokens") || "null");
    } catch (e) {
      tokens = null;
    }
    if (!tokens || !tokens.refresh_token) throw new Error("Open Home Assistant in this browser and log in first.");
    if (tokens.access_token && tokens.expires && tokens.expires > Date.now() + 30000) return tokens.access_token;
    const body = new URLSearchParams({
      grant_type: "refresh_token",
      refresh_token: tokens.refresh_token,
      client_id: tokens.clientId || location.origin + "/",
    });
    const resp = await fetch("/auth/token", { method: "POST", body });
    if (!resp.ok) throw new Error("Home Assistant login expired. Open Home Assistant and log in again.");
    const fresh = await resp.json();
    tokens.access_token = fresh.access_token;
    tokens.expires = Date.now() + fresh.expires_in * 1000;
    try {
      localStorage.setItem("hassTokens", JSON.stringify(tokens));
    } catch (e) {
      /* ignore */
    }
    return tokens.access_token;
  }

  function openSocket() {
    if (socketReady) return socketReady;
    socketReady = new Promise((resolve, reject) => {
      const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/websocket`);
      ws.onmessage = async (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "auth_required") {
          try {
            ws.send(JSON.stringify({ type: "auth", access_token: await accessToken() }));
          } catch (err) {
            reject(err);
          }
        } else if (msg.type === "auth_ok") {
          socket = ws;
          resolve(ws);
        } else if (msg.type === "auth_invalid") {
          reject(new Error("Home Assistant rejected the login."));
        } else if (msg.type === "result" && pending.has(msg.id)) {
          const p = pending.get(msg.id);
          pending.delete(msg.id);
          msg.success ? p.resolve(msg.result) : p.reject(new Error((msg.error && msg.error.message) || "Request failed"));
        } else if (msg.type === "event" && subscribers.has(msg.id)) {
          subscribers.get(msg.id)(msg.event);
        }
      };
      ws.onclose = () => {
        socket = null;
        socketReady = null;
        pending.forEach((p) => p.reject(new Error("Connection to Home Assistant lost.")));
        pending.clear();
      };
      ws.onerror = () => reject(new Error("Could not connect to Home Assistant."));
    });
    return socketReady;
  }

  async function standaloneCall(message) {
    const ws = await openSocket();
    const id = nextId++;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ ...message, id }));
    });
  }

  // ---- public API -----------------------------------------------------------
  async function callWS(message) {
    for (let i = 0; i < 20; i++) {
      const h = findHass();
      if (h) {
        try {
          return await h.callWS(message);
        } catch (err) {
          throw new Error((err && err.message) || "Request failed");
        }
      }
      if (window.parent === window) break; // standalone: don't wait for a kiosk
      await sleep(250);
    }
    return standaloneCall(message);
  }

  async function subscribeEvents(eventType, callback) {
    const h = findHass();
    if (h) return h.connection.subscribeEvents(callback, eventType);
    const ws = await openSocket();
    const id = nextId++;
    subscribers.set(id, callback);
    ws.send(JSON.stringify({ id, type: "subscribe_events", event_type: eventType }));
    return () => subscribers.delete(id);
  }

  function states() {
    const h = findHass();
    return h ? h.states : {};
  }

  // Tell the kiosk what we're doing (it hides the dock / skips the screensaver).
  function toKiosk(type, extra) {
    if (window.parent !== window) window.parent.postMessage(Object.assign({ type }, extra || {}), location.origin);
  }

  function clock(el) {
    const tick = () => {
      el.textContent = new Date().toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    };
    tick();
    setInterval(tick, 10000);
  }

  window.Frostbox = { callWS, subscribeEvents, states, toKiosk, clock, sleep };
})();
