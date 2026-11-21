import json
import os
import subprocess
import sys
import threading
import webbrowser

import webview

import core

HERE = os.path.dirname(os.path.abspath(__file__))
VIDEO_EXT = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".gif", ".flv", ".wmv", ".m4v", ".mpg", ".mpeg")


class Api:
    def __init__(self):
        self._window = None
        self._ff = core.find_ffmpeg()
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._reset()

    def _reset(self):
        self._st = {"running": False, "progress": 0.0, "phase": "", "log": [],
                    "done": False, "error": None, "result": None, "cancelled": False}

    def init(self):
        return {"ffmpeg": bool(self._ff)}

    def open_url(self, url):
        if url.startswith("https://"):
            webbrowser.open(url)

    def reveal(self, path):
        path = os.path.normpath(path)
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])

    def _dialog(self, kind):
        fd = getattr(webview, "FileDialog", None)
        if fd is not None:
            return getattr(fd, kind)
        return {"OPEN": webview.OPEN_DIALOG, "SAVE": webview.SAVE_DIALOG}[kind]

    def pick_video(self):
        types = ("Video (*.mp4;*.mkv;*.avi;*.mov;*.webm;*.gif;*.flv;*.wmv;*.m4v)", "All files (*.*)")
        r = self._window.create_file_dialog(self._dialog("OPEN"), allow_multiple=False, file_types=types)
        return r[0] if r else None

    def pick_save(self, suggested):
        d, n = os.path.split(suggested)
        r = self._window.create_file_dialog(self._dialog("SAVE"), directory=d, save_filename=n,
                                            file_types=("Scratch 3 (*.sb3)",))
        if not r:
            return None
        p = r if isinstance(r, str) else r[0]
        return p if p.lower().endswith(".sb3") else p + ".sb3"

    def probe(self, path):
        if not self._ff:
            return {"error": "ffmpeg not found. Run the program via start.bat."}
        try:
            info = core.probe(self._ff, path)
        except Exception as e:
            return {"error": str(e)}
        if not info["has_video"]:
            return {"error": "This file has no video track."}
        info["path"] = path
        info["out"] = os.path.splitext(path)[0] + ".sb3"
        return info

    def preview(self, path, opts):
        try:
            return core.preview(self._ff, path, opts)
        except Exception as e:
            return {"error": str(e)}

    def start(self, path, out, opts):
        with self._lock:
            if self._st["running"]:
                return False
            self._reset()
            self._st["running"] = True
        self._cancel.clear()
        o = dict(opts, video=path, out=out, ffmpeg=self._ff)

        def log(m):
            self._st["log"].append(m)

        def prog(p, phase):
            self._st["progress"], self._st["phase"] = p, phase

        def work():
            try:
                self._st["result"] = core.convert(o, log, prog, self._cancel)
                self._st["done"] = True
            except core.Cancelled:
                self._st["cancelled"] = True
                try:
                    os.remove(out)
                except OSError:
                    pass
            except Exception as e:
                self._st["error"] = str(e)
            finally:
                self._st["running"] = False

        threading.Thread(target=work, daemon=True).start()
        return True

    def state(self):
        s = dict(self._st)
        s["log"] = list(s["log"])[-60:]
        return s

    def cancel(self):
        self._cancel.set()


def main():
    api = Api()
    window = webview.create_window(
        "Video → Scratch", url=os.path.join(HERE, "ui", "index.html"), js_api=api,
        width=1120, height=780, min_size=(980, 700), background_color="#ece7dd")
    api._window = window

    def on_drop(e):
        try:
            files = e["dataTransfer"]["files"]
            if files:
                p = files[0].get("pywebviewFullPath")
                if p and p.lower().endswith(VIDEO_EXT):
                    window.evaluate_js(f"App.setVideo({json.dumps(p)})")
                else:
                    window.evaluate_js("App.toast('This is not a video file')")
        except Exception:
            pass

    def bind():
        try:
            from webview.dom import DOMEventHandler
            window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
            window.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True)
        except Exception:
            pass

    webview.start(bind)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        with open(os.path.join(HERE, "error.log"), "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        raise