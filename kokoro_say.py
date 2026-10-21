#!/usr/bin/env python3
"""kokoro-say: speak selected or clipboard text with Kokoro-82M, locally.

`serve` runs a small daemon that keeps the model loaded and listens on a
Unix socket; every other subcommand is a thin client, so a hotkey costs a
socket round trip instead of a model load. The client never imports
kokoro_onnx, which keeps it fast.
"""

import argparse
import json
import os
import queue
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
SOCKET_PATH = RUNTIME_DIR / "kokoro-say.sock"
CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kokoro-say" / "config.toml"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "kokoro-say"

DEFAULTS = {
    "voice": "bf_emma",
    "speed": 1.0,
    "lang": "en-gb",
    "model": str(DATA_DIR / "models" / "kokoro-v1.0.onnx"),
    "voices": str(DATA_DIR / "models" / "voices-v1.0.bin"),
    # "auto" uses CUDA when onnxruntime-gpu can load it, else the CPU.
    "provider": "auto",
    "max_chars": 20000,
    "sentence_pause": 0.25,
}


def load_config():
    config = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            config.update(tomllib.load(f))
    return config


def notify(message):
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "kokoro-say", "Kokoro", message], check=False)


# --------------------------------------------------------------------- daemon

def split_sentences(text):
    """Split into sentence-sized pieces so speech starts after the first one."""
    pieces = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        if not paragraph:
            continue
        for sentence in re.split(r"(?<=[.!?;:])\s+(?=[\"'(\[]?[A-Z0-9])", paragraph):
            # Kokoro handles long input, but shorter chunks start sooner.
            while len(sentence) > 300:
                cut = sentence.rfind(", ", 0, 300)
                cut = cut + 1 if cut > 80 else 300
                pieces.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if sentence:
                pieces.append(sentence)
    return pieces


class Speaker:
    def __init__(self, config):
        self.config = config
        self.kokoro, self.provider = self._load_model()
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.player = None
        self.thread = None

    def _load_model(self):
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        wanted = self.config["provider"]
        available = ort.get_available_providers()
        if wanted == "auto":
            wanted = "CUDAExecutionProvider" if "CUDAExecutionProvider" in available else "CPUExecutionProvider"
        if wanted == "CUDAExecutionProvider" and hasattr(ort, "preload_dlls"):
            # Loads the CUDA/cuDNN libraries shipped as pip wheels.
            ort.preload_dlls()
        options = ort.SessionOptions()
        options.log_severity_level = 3  # CUDA logs harmless Memcpy/ScatterND warnings on every start
        session = ort.InferenceSession(self.config["model"], options, providers=[wanted])
        provider = session.get_providers()[0]
        return Kokoro.from_session(session, self.config["voices"]), provider

    def speaking(self):
        return self.thread is not None and self.thread.is_alive()

    def stop(self):
        with self.lock:
            self.cancel.set()
            if self.player and self.player.poll() is None:
                self.player.kill()
            thread = self.thread
        if thread:
            thread.join(timeout=5)

    def say(self, text, voice=None, speed=None):
        self.stop()
        text = text[: int(self.config["max_chars"])]
        pieces = split_sentences(text)
        if not pieces:
            return False
        with self.lock:
            self.cancel = threading.Event()
            self.thread = threading.Thread(
                target=self._run,
                args=(pieces, voice or self.config["voice"], float(speed or self.config["speed"]), self.cancel),
                daemon=True,
            )
            self.thread.start()
        return True

    def _run(self, pieces, voice, speed, cancel):
        audio = queue.Queue(maxsize=4)

        def generate():
            try:
                for piece in pieces:
                    if cancel.is_set():
                        break
                    samples, rate = self.kokoro.create(piece, voice=voice, speed=speed, lang=self.config["lang"])
                    audio.put((samples, rate))
            except Exception as e:  # surfaced, not swallowed: a bad voice name lands here
                print(f"generation failed: {e}", file=sys.stderr, flush=True)
                notify(f"Speech failed: {e}")
            finally:
                audio.put(None)

        threading.Thread(target=generate, daemon=True).start()
        player = None
        try:
            while not cancel.is_set():
                item = audio.get()
                if item is None:
                    break
                samples, rate = item
                if player is None:
                    player = subprocess.Popen(
                        ["pw-play", "--raw", "--rate", str(rate), "--channels", "1", "--format", "f32", "-"],
                        stdin=subprocess.PIPE,
                    )
                    with self.lock:
                        self.player = player
                    if cancel.is_set():
                        player.kill()
                        break
                # Kokoro trims edge silence, so add the gap between sentences back.
                gap = bytes(4 * int(rate * float(self.config["sentence_pause"])))
                player.stdin.write(samples.astype("float32").tobytes() + gap)
                player.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass  # stop() killed the player mid-write
        finally:
            if player:
                try:
                    player.stdin.close()
                except (BrokenPipeError, ValueError):
                    pass
                # A player that dies on its own (bad format, no audio device)
                # must not fail silently; one killed by stop() is expected.
                if player.wait() != 0 and not cancel.is_set():
                    print(f"pw-play exited with {player.returncode}", file=sys.stderr, flush=True)
                    notify(f"Audio playback failed (pw-play exit {player.returncode})")
            # Drain so the generator thread can't block on a full queue.
            while not audio.empty():
                audio.get_nowait()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        speaker = self.server.speaker
        try:
            request = json.loads(self.rfile.readline())
            cmd = request.get("cmd")
            if cmd == "say":
                ok = speaker.say(request.get("text", ""), request.get("voice"), request.get("speed"))
                reply = {"ok": ok, "state": "speaking" if ok else "empty"}
            elif cmd == "toggle":
                if speaker.speaking():
                    speaker.stop()
                    reply = {"ok": True, "state": "stopped"}
                else:
                    ok = speaker.say(request.get("text", ""), request.get("voice"), request.get("speed"))
                    reply = {"ok": ok, "state": "speaking" if ok else "empty"}
            elif cmd == "stop":
                speaker.stop()
                reply = {"ok": True, "state": "stopped"}
            elif cmd == "status":
                reply = {
                    "ok": True,
                    "state": "speaking" if speaker.speaking() else "idle",
                    "provider": speaker.provider,
                    "voice": speaker.config["voice"],
                }
            elif cmd == "voices":
                reply = {"ok": True, "voices": sorted(speaker.kokoro.get_voices())}
            else:
                reply = {"ok": False, "error": f"unknown command {cmd!r}"}
        except Exception as e:
            reply = {"ok": False, "error": str(e)}
        self.wfile.write((json.dumps(reply) + "\n").encode())


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve(_args):
    config = load_config()
    for key in ("model", "voices"):
        if not Path(config[key]).exists():
            sys.exit(f"missing {key} file: {config[key]} (run install.sh)")
    started = time.monotonic()
    speaker = Speaker(config)
    print(f"model loaded in {time.monotonic() - started:.1f}s on {speaker.provider}", flush=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()
    with Server(str(SOCKET_PATH), Handler) as server:
        os.chmod(SOCKET_PATH, 0o600)
        server.speaker = speaker
        print(f"listening on {SOCKET_PATH}", flush=True)
        server.serve_forever()


# --------------------------------------------------------------------- client

def request(payload, timeout=10):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(SOCKET_PATH))
            s.sendall((json.dumps(payload) + "\n").encode())
            return json.loads(s.makefile().readline())
    except (FileNotFoundError, ConnectionRefusedError):
        message = "Daemon not running. Start it: systemctl --user start kokoro-say"
        notify(message)
        sys.exit(message)


def read_source(args):
    if args.text is not None:
        return args.text
    if args.source == "stdin":
        return sys.stdin.read()
    cmd = ["wl-paste", "--no-newline", "--type", "text"]
    if args.source == "primary":
        cmd.insert(1, "--primary")
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


def speak(args, cmd):
    text = read_source(args)
    reply = request({"cmd": cmd, "text": text, "voice": args.voice, "speed": args.speed})
    if reply.get("state") == "empty":
        notify(f"Nothing to read ({args.source} is empty)")
    if not reply.get("ok") and reply.get("error"):
        notify(reply["error"])
        sys.exit(reply["error"])


def main():
    parser = argparse.ArgumentParser(prog="kokoro-say", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="run the daemon (normally via systemd)")
    for name, help_text in (("say", "speak text, replacing anything already playing"),
                            ("toggle", "stop if speaking, otherwise speak")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("source", nargs="?", default="primary", choices=("primary", "clipboard", "stdin"),
                       help="primary = highlighted text (default), clipboard, or stdin")
        p.add_argument("-t", "--text", help="speak this text instead of reading a source")
        p.add_argument("-v", "--voice", help="voice id, e.g. bf_emma, af_heart (see `voices`)")
        p.add_argument("-s", "--speed", type=float, help="speech speed, 1.0 is normal")
    sub.add_parser("stop", help="stop speaking")
    sub.add_parser("status", help="show daemon state")
    sub.add_parser("voices", help="list available voices")

    args = parser.parse_args()
    if args.command == "serve":
        serve(args)
    elif args.command in ("say", "toggle"):
        speak(args, args.command)
    elif args.command == "voices":
        print("\n".join(request({"cmd": "voices"})["voices"]))
    else:
        reply = request({"cmd": args.command})
        print(json.dumps(reply) if args.command == "status" else reply.get("state", reply))


if __name__ == "__main__":
    main()
