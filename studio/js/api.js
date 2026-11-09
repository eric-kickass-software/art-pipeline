// The Studio's op registry from the pages (hdart/core/ops.py, served by hdart/server/studio.py).
//   get("dungeon.state", {set})      GET  /api/op/<name>?...   read-only ops (list/dict arguments as JSON text)
//   post("dungeon.approve", {...})   POST /api/op/<name>       every other op (a JSON object of arguments)
//   url("limner.image", {path}, t)   the GET URL of an op that returns an image (for <img src>); t = cache buster
// Ops are strict: an argument the op doesn't take is refused (400), so send exactly its parameters. Every failure
// comes back as {error, type} with a 4xx/5xx status and is thrown here as an ApiError carrying that text, so a page
// has one error path: catch it and show e.message.

export class ApiError extends Error {
  constructor(message, status = 0, type = "") {
    super(message);
    this.status = status;
    this.type = type;
  }
}

const LONG_QUERY = 6000;   // a GET longer than this (big lists or objects) goes as a POST: the op is the same

function query(args) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(args || {})) {
    if (v === undefined || v === null) continue;   // left out: the op's default
    q.append(k, typeof v === "object" ? JSON.stringify(v) : String(v));
  }
  return q.toString();
}

async function send(path, init) {
  let r;
  try { r = await fetch(path, init); }
  catch (e) { throw new ApiError(`the Studio didn't answer (${e.message})`); }
  let body = null;
  try { body = await r.json(); } catch (e) { /* not JSON: an HTTP error page */ }
  if (!r.ok) {
    throw new ApiError(body && body.error ? body.error : `${r.status} ${r.statusText}`, r.status, (body && body.type) || "");
  }
  return body;
}

/** Call a read-only op over GET (falls back to POST when the arguments are too long for a URL). */
export function get(name, args = {}) {
  const q = query(args);
  if (q.length > LONG_QUERY) return post(name, args);
  return send(`/api/op/${name}${q ? "?" + q : ""}`);
}

/** Call an op with a JSON object of arguments. */
export function post(name, args = {}) {
  return send(`/api/op/${name}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args) });
}

/** The URL of a read-only op that returns an image; `stamp` (optional) busts the browser cache. */
export function url(name, args = {}, stamp) {
  const q = query(stamp === undefined ? args : { ...args, _: stamp });
  return `/api/op/${name}${q ? "?" + q : ""}`;
}
