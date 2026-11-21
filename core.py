import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile

STAGE_W, STAGE_H = 480, 360
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HERE = os.path.dirname(os.path.abspath(__file__))


class Cancelled(Exception):
    pass


def find_ffmpeg():
    for name in ("ffmpeg.exe", "ffmpeg"):
        p = os.path.join(HERE, "bin", name)
        if os.path.isfile(p):
            return p
    return shutil.which("ffmpeg")


def _info_text(ff, video):
    p = subprocess.run([ff, "-i", video], capture_output=True, errors="replace",
                       text=True, creationflags=NOWIN)
    return p.stderr


def probe(ff, video):
    t = _info_text(ff, video)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", t)
    if not m:
        raise RuntimeError("Could not read the video. Is this really a video file?")
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    vl = re.search(r"Video:.*", t)
    w = h = 0
    fps = 0.0
    if vl:
        mm = re.search(r"[ ,](\d{2,5})x(\d{2,5})", vl.group(0))
        if mm:
            w, h = int(mm.group(1)), int(mm.group(2))
        mf = re.search(r"([\d.]+) fps", vl.group(0))
        if mf:
            fps = float(mf.group(1))
    return {"name": os.path.basename(video), "duration": dur, "w": w, "h": h,
            "fps": fps, "audio": "Audio:" in t, "has_video": bool(vl),
            "size": os.path.getsize(video)}


def _scale(fit):
    w, h = STAGE_W, STAGE_H
    if fit == "fill":
        return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    if fit == "stretch":
        return f"scale={w}:{h}"
    return (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")


def _qv(quality):
    return str(int(round(2 + (100 - int(quality)) * 29 / 100)))


def preview(ff, video, opts):
    info = probe(ff, video)
    dur = info["duration"]
    tmp = tempfile.mkdtemp(prefix="v2sb3p_")
    try:
        out = os.path.join(tmp, "p.jpg")
        vf = _scale(opts["fit"]) + ",format=yuvj420p"
        subprocess.run([ff, "-y", "-v", "error", "-ss", f"{dur * 0.3:.2f}", "-i", video,
                        "-an", "-frames:v", "1", "-vf", vf, "-q:v", _qv(opts["quality"]), out],
                       capture_output=True, creationflags=NOWIN)
        if not os.path.isfile(out):
            subprocess.run([ff, "-y", "-v", "error", "-i", video, "-an", "-frames:v", "1",
                            "-vf", vf, "-q:v", _qv(opts["quality"]), out],
                           capture_output=True, creationflags=NOWIN)
        data = open(out, "rb").read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    frames = max(1, int(dur * int(opts["fps"])))
    mb = len(data) * frames / 1e6
    if opts.get("sound") and info["audio"]:
        mb += dur * 12000 / 1e6
    return {"img": "data:image/jpeg;base64," + base64.b64encode(data).decode(),
            "frames": frames, "mb": mb}


def _run(cmd, cancel, on_tick=None):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=NOWIN)
    buf = []
    t = threading.Thread(target=lambda: buf.append(proc.stdout.read()), daemon=True)
    t.start()
    while proc.poll() is None:
        if cancel.is_set():
            proc.kill()
            raise Cancelled()
        if on_tick:
            on_tick()
        time.sleep(0.2)
    t.join()
    if proc.returncode != 0:
        out = (buf[0] if buf else b"").decode("utf-8", "replace")
        raise RuntimeError("ffmpeg exited with an error:\n" + out[-800:])


def convert(opts, log, progress, cancel):
    ff, fps, video = opts["ffmpeg"], int(opts["fps"]), opts["video"]
    info = probe(ff, video)
    duration = info["duration"]
    total = max(1, int(duration * fps))
    tmp = tempfile.mkdtemp(prefix="v2sb3_")
    try:
        log(f"Video {duration:.1f} s → approximately {total} frames ({fps} fps)")
        vf = f"fps={fps},{_scale(opts['fit'])},format=yuvj420p"
        pattern = os.path.join(tmp, "f_%06d.jpg")

        def tick():
            n = sum(1 for f in os.listdir(tmp) if f.startswith("f_"))
            progress(min(0.7, n / total * 0.7), "Extracting frames")

        progress(0, "Extracting frames")
        log("Extracting frames...")
        _run([ff, "-y", "-v", "error", "-i", video, "-an", "-vf", vf,
              "-q:v", _qv(opts["quality"]), pattern], cancel, tick)
        frames = sorted(f for f in os.listdir(tmp) if f.startswith("f_"))
        if not frames:
            raise RuntimeError("ffmpeg did not output any frames.")
        log(f"Frames extracted: {len(frames)}")

        sound = None
        if opts["sound"] and info["audio"]:
            progress(0.72, "Extracting audio")
            log("Extracting audio...")
            try:
                ap = os.path.join(tmp, "audio.mp3")
                _run([ff, "-y", "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", "44100",
                      "-c:a", "libmp3lame", "-b:a", "96k", ap], cancel)
                sound = (ap, "mp3", 44100)
            except RuntimeError:
                ap = os.path.join(tmp, "audio.wav")
                _run([ff, "-y", "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", "22050",
                      "-c:a", "pcm_s16le", ap], cancel)
                sound = (ap, "wav", 22050)
        elif opts["sound"]:
            log("Video has no audio — skipping.")

        progress(0.76, "Building project")
        log("Building .sb3...")
        md5 = lambda b: hashlib.md5(b).hexdigest()
        costumes, written = [], set()
        with zipfile.ZipFile(opts["out"], "w", zipfile.ZIP_STORED) as z:
            for i, f in enumerate(frames):
                if cancel.is_set():
                    raise Cancelled()
                data = open(os.path.join(tmp, f), "rb").read()
                hsh = md5(data)
                name = hsh + ".jpg"
                if name not in written:
                    z.writestr(name, data)
                    written.add(name)
                costumes.append({"assetId": hsh, "name": "f%06d" % (i + 1),
                                 "bitmapResolution": 1, "md5ext": name, "dataFormat": "jpg",
                                 "rotationCenterX": STAGE_W // 2, "rotationCenterY": STAGE_H // 2})
                if i % 100 == 0:
                    progress(0.76 + 0.22 * i / len(frames), "Building project")

            svg = (b'<svg version="1.1" width="2" height="2" viewBox="-1 -1 2 2" '
                   b'xmlns="http://www.w3.org/2000/svg"></svg>')
            sh = md5(svg)
            z.writestr(sh + ".svg", svg)

            sounds = []
            if sound:
                sdata = open(sound[0], "rb").read()
                ah = md5(sdata)
                z.writestr(f"{ah}.{sound[1]}", sdata)
                sounds.append({"assetId": ah, "name": "sound", "dataFormat": sound[1],
                               "format": "", "rate": sound[2],
                               "sampleCount": int(duration * sound[2]),
                               "md5ext": f"{ah}.{sound[1]}"})

            blocks = build_blocks(len(frames), fps, int(opts["spacing"]), bool(sounds))
            proj = {
                "targets": [
                    {"isStage": True, "name": "Stage", "variables": {}, "lists": {},
                     "broadcasts": {"bc1": "play"}, "blocks": {}, "comments": {},
                     "currentCostume": 0,
                     "costumes": [{"assetId": sh, "name": "backdrop1", "md5ext": sh + ".svg",
                                   "dataFormat": "svg", "rotationCenterX": 1, "rotationCenterY": 1}],
                     "sounds": [], "volume": 100, "layerOrder": 0, "tempo": 60,
                     "videoTransparency": 50, "videoState": "on", "textToSpeechLanguage": None},
                    {"isStage": False, "name": "Video", "variables": {}, "lists": {},
                     "broadcasts": {}, "blocks": blocks, "comments": {}, "currentCostume": 0,
                     "costumes": costumes, "sounds": sounds, "volume": 100, "layerOrder": 1,
                     "visible": True, "x": 0, "y": 0, "size": 100, "direction": 90,
                     "draggable": False, "rotationStyle": "all around"}],
                "monitors": [], "extensions": [],
                "meta": {"semver": "3.0.0", "vm": "0.2.0", "agent": ""}}
            z.writestr("project.json", json.dumps(proj))
        progress(1.0, "Done")
        mb = os.path.getsize(opts["out"]) / 1e6
        log(f"Done: {mb:.1f} MB, {len(costumes)} costumes")
        return {"out": opts["out"], "mb": mb, "costumes": len(costumes)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build_blocks(n_frames, fps, spacing, with_sound):
    D = spacing
    END = round(n_frames / fps, 2)

    def blk(op, next=None, parent=None, inputs=None, fields=None, top=False,
            shadow=False, x=0, y=0, mut=None):
        b = {"opcode": op, "next": next, "parent": parent, "inputs": inputs or {},
             "fields": fields or {}, "shadow": shadow, "topLevel": top}
        if top:
            b["x"], b["y"] = x, y
        if mut:
            b["mutation"] = mut
        return b

    stop_mut = {"tagName": "mutation", "children": [], "hasnext": "false"}
    B = {}
    B["a1"] = blk("event_whenflagclicked", "a2", top=True, x=0, y=0)
    B["a2"] = blk("motion_gotoxy", "a3", "a1", {"X": [1, [4, "0"]], "Y": [1, [4, "0"]]})
    B["a3"] = blk("looks_setsizeto", "a4", "a2", {"SIZE": [1, [4, "100"]]})
    B["a4"] = blk("looks_switchcostumeto", "a5", "a3", {"COSTUME": [1, "am"]})
    B["am"] = blk("looks_costume", None, "a4", fields={"COSTUME": ["f000001", None]}, shadow=True)
    B["a5"] = blk("event_broadcast", None, "a4", {"BROADCAST_INPUT": [1, [11, "play", "bc1"]]})

    B["b1"] = blk("event_whenbroadcastreceived", "b2" if with_sound else "b3", top=True, x=D, y=0,
                  fields={"BROADCAST_OPTION": ["play", "bc1"]})
    prev = "b1"
    if with_sound:
        B["b2"] = blk("sound_play", "b3", "b1", {"SOUND_MENU": [1, "bm"]})
        B["bm"] = blk("sound_sounds_menu", None, "b2", fields={"SOUND_MENU": ["sound", None]}, shadow=True)
        prev = "b2"
    B["b3"] = blk("sensing_resettimer", "b4", prev)
    B["b4"] = blk("control_repeat_until", "b5", "b3", {"CONDITION": [2, "gt"], "SUBSTACK": [2, "sw"]})
    B["gt"] = blk("operator_gt", None, "b4", {"OPERAND1": [3, "tm", [10, ""]], "OPERAND2": [1, [10, str(END)]]})
    B["tm"] = blk("sensing_timer", None, "gt")
    B["sw"] = blk("looks_switchcostumeto", None, "b4", {"COSTUME": [3, "add", "sm"]})
    B["sm"] = blk("looks_costume", None, "sw", fields={"COSTUME": ["f000001", None]}, shadow=True)
    B["add"] = blk("operator_add", None, "sw", {"NUM1": [3, "rnd", [4, ""]], "NUM2": [1, [4, "1"]]})
    B["rnd"] = blk("operator_round", None, "add", {"NUM": [3, "mul", [4, ""]]})
    B["mul"] = blk("operator_multiply", None, "rnd", {"NUM1": [3, "tm2", [4, ""]], "NUM2": [1, [4, str(fps)]]})
    B["tm2"] = blk("sensing_timer", None, "mul")
    B["b5"] = blk("control_stop", None, "b4", fields={"STOP_OPTION": ["all", None]}, mut=stop_mut)

    B["c1"] = blk("event_whenkeypressed", "c2", top=True, x=0, y=D, fields={"KEY_OPTION": ["space", None]})
    B["c2"] = blk("control_stop", None, "c1", fields={"STOP_OPTION": ["all", None]}, mut=stop_mut)

    B["d1"] = blk("event_whenthisspriteclicked", "d2", top=True, x=D, y=D)
    B["d2"] = blk("event_broadcast", None, "d1", {"BROADCAST_INPUT": [1, [11, "play", "bc1"]]})
    return B