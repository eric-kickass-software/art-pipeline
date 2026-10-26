"""A fake ComfyUI for tests: the same HTTP endpoints hdart.comfy uses, answered in-process with deterministic images,
so every flow runs without the GPU and never queues behind the real ComfyUI (the live Studio shares it).

    with FakeComfy() as fake:          # starts on a free port and points hdart.comfy at it
        img = comfy.img2img(init, "prompt", seed=1, denoise=0.5)
        fake.prompts[-1]               # the graph it received

What a workflow returns (override with `respond`): the first LoadImage input unchanged (so an img2img/edit/repaint
pass is the identity), else for txt2img a flat colour from the seed at the latent's size, with a transparent border
when the graph removes the background. Set `fail` to make the next workflow fail, `busy` to fake other clients'
queued work, `delay` to keep a workflow running (for interrupt tests).
"""
import io
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np
from PIL import Image

from . import comfy


def _classes(nodes):
    return [n["class_type"] for n in nodes.values()]


def info(nodes, uploads):
    """What a test usually wants to know about a workflow: {"prompt", "init" (PIL or None), "mask" (bool array or
    None), "refs" (uploaded names of the other LoadImage inputs), "classes"}."""
    texts = [n["inputs"]["text"] for n in nodes.values() if n["class_type"] == "CLIPTextEncode"]
    loads = [n["inputs"]["image"] for n in nodes.values() if n["class_type"] == "LoadImage"]
    masks = [n["inputs"]["image"] for n in nodes.values() if n["class_type"] == "LoadImageMask"]
    return {"prompt": texts[0] if texts else None,
            "init": Image.open(io.BytesIO(uploads[loads[0]])) if loads else None,
            "mask": np.asarray(Image.open(io.BytesIO(uploads[masks[0]])).convert("L")) > 127 if masks else None,
            "refs": loads[1:], "classes": _classes(nodes)}


def default_respond(nodes, uploads):
    """The images a workflow returns: one per SaveImage node (times the batch for txt2img)."""
    saves = [n for n in nodes.values() if n["class_type"] == "SaveImage"]
    loads = [n["inputs"]["image"] for n in nodes.values() if n["class_type"] == "LoadImage"]
    if loads:
        src = Image.open(io.BytesIO(uploads[loads[0]]))
        src.load()
        return [src] * len(saves)
    lat = next((n["inputs"] for n in nodes.values() if n["class_type"] == "EmptyFlux2LatentImage"), {})
    w, h, batch = int(lat.get("width", 64)), int(lat.get("height", 64)), int(lat.get("batch_size", 1))
    seed = next((int(n["inputs"]["noise_seed"]) for n in nodes.values() if n["class_type"] == "RandomNoise"), 0)
    colour = ((seed * 67) % 200 + 30, (seed * 131) % 200 + 30, (seed * 197) % 200 + 30)
    if "RemoveBackground" in _classes(nodes):
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        im.paste(Image.new("RGBA", (w // 2, h // 2), colour + (255,)), (w // 4, h // 4))
    else:
        im = Image.new("RGB", (w, h), colour)
    return [im] * batch * len(saves)


class FakeComfy:
    def __init__(self, respond=default_respond):
        self.respond, self.uploads, self.prompts, self.files, self.history = respond, {}, [], {}, {}
        self.fail, self.busy, self.delay, self.running, self._interrupt = None, 0, 0.0, None, threading.Event()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, body, ctype="application/json", code=200):
                if not isinstance(body, bytes):
                    body = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/system_stats":
                    return self._send({"system": {"fake": True}})
                if u.path == "/queue":
                    run = [["x", "other"]] * min(fake.busy, 1) + ([["x", fake.running]] if fake.running else [])
                    return self._send({"queue_running": run, "queue_pending": [["x", "other"]] * max(fake.busy - 1, 0)})
                if u.path.startswith("/history/"):
                    pid = u.path.rsplit("/", 1)[1]
                    return self._send({pid: fake.history[pid]} if pid in fake.history else {})
                if u.path == "/view":
                    name = parse_qs(u.query)["filename"][0]
                    return self._send(fake.files[name], "image/png")
                self._send({"error": "not found"}, code=404)

            def do_POST(self):
                u = urlparse(self.path)
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if u.path == "/upload/image":
                    name, data = _multipart_file(body, self.headers["Content-Type"])
                    fake.uploads[name] = data
                    return self._send({"name": name, "subfolder": "", "type": "input"})
                if u.path == "/prompt":
                    nodes = json.loads(body)["prompt"]
                    bad = [n for n in nodes.values() if n["class_type"] == "LoadImage" and n["inputs"]["image"] not in fake.uploads]
                    if bad:
                        return self._send({"error": "missing input image", "node_errors": bad}, code=400)
                    fake.prompts.append(nodes)
                    pid = uuid.uuid4().hex
                    fake.history[pid] = {"status": {"completed": False}, "outputs": {}}
                    threading.Thread(target=fake._execute, args=(pid, nodes), daemon=True).start()
                    return self._send({"prompt_id": pid, "number": len(fake.prompts)})
                if u.path == "/interrupt":
                    fake._interrupt.set()
                    return self._send({})
                self._send({"error": "not found"}, code=404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _execute(self, pid, nodes):
        self.running = pid
        self._interrupt.clear()
        t0 = time.time()
        while time.time() - t0 < self.delay and not self._interrupt.is_set():
            time.sleep(0.02)
        self.running = None
        if self._interrupt.is_set() or self.fail:
            kind = "execution_interrupted" if self._interrupt.is_set() else "execution_error"
            msg, self.fail = self.fail or "interrupted", None
            self.history[pid] = {"status": {"status_str": "error", "completed": False,
                                            "messages": [[kind, {"exception_message": msg}]]}, "outputs": {}}
            return
        files = []
        for k, im in enumerate(self.respond(nodes, self.uploads)):
            buf = io.BytesIO()
            im.save(buf, "PNG")
            name = f"fake_{pid[:8]}_{k:02d}.png"
            self.files[name] = buf.getvalue()
            files.append({"filename": name, "subfolder": "", "type": "output"})
        self.history[pid] = {"status": {"status_str": "success", "completed": True}, "outputs": {"9": {"images": files}}}

    def start(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self._prev, comfy.URL = comfy.URL, self.url
        return self

    def stop(self):
        comfy.URL = self._prev
        self.server.shutdown()
        self.server.server_close()

    __enter__ = start

    def __exit__(self, *a):
        self.stop()


def _multipart_file(body, ctype):
    """(filename, bytes) of the file part of a multipart/form-data body (what requests sends for upload())."""
    boundary = ctype.split("boundary=", 1)[1].strip('"').encode()
    for part in body.split(b"--" + boundary):
        head, _, data = part.partition(b"\r\n\r\n")
        if b'name="image"' in head and b"filename=" in head:
            name = head.split(b'filename="', 1)[1].split(b'"', 1)[0].decode()
            return name, data[:-2] if data.endswith(b"\r\n") else data
    raise ValueError("no file in the upload")
