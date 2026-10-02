#!/usr/bin/env python3
"""Muxes the ElevenLabs narration clips onto the recorded kiosk loop.

Inputs:
  kiosk/demo-video/timeline.json  (from scripts/record-demo.mjs)
  clip dir with clip-01.mp3 .. clip-11.mp3 (arg 1, default /tmp/tokcop-audio)
Output:
  kiosk/demo-video/token-cop-demo.mp4

Each clip starts ~0.4s after its scene appears; if the previous clip is still
playing (clip 5 runs long), the next start cascades to avoid overlap.
"""
import json
import pathlib
import subprocess
import sys

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "demo-video"
CLIP_DIR = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/tokcop-audio")

timeline = json.loads((OUT_DIR / "timeline.json").read_text())
starts = [e["t"] for e in timeline["events"][:-1]]  # last event is the wrap
t_start, t_end = starts[0], timeline["tEnd"]


def duration(path: pathlib.Path) -> float:
    out = subprocess.run(["afinfo", str(path)], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "estimated duration" in line:
            return float(line.split()[2])
    raise SystemExit(f"no duration for {path}")


clips = sorted(CLIP_DIR.glob("clip-*.mp3"))
if len(clips) != len(starts):
    raise SystemExit(f"{len(clips)} clips but {len(starts)} scenes")

offsets, prev_end = [], 0.0
for clip, scene_start in zip(clips, starts):
    off = max(scene_start - t_start + 0.4, prev_end + 0.3)
    offsets.append(off)
    prev_end = off + duration(clip)

inputs = ["-ss", f"{t_start}", "-i", timeline["videoPath"]]
for clip in clips:
    inputs += ["-i", str(clip)]
delays = ";".join(f"[{i + 1}:a]adelay={int(off * 1000)}:all=1[a{i}]" for i, off in enumerate(offsets))
labels = "".join(f"[a{i}]" for i in range(len(offsets)))
filter_complex = f"{delays};{labels}amix=inputs={len(offsets)}:normalize=0,loudnorm=I=-18:TP=-1.5[aout]"

out = OUT_DIR / "token-cop-demo.mp4"
cmd = [
    "ffmpeg", "-y", *inputs,
    "-filter_complex", filter_complex,
    "-map", "0:v", "-map", "[aout]",
    "-t", f"{t_end - t_start}",
    "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "160k",
    str(out),
]
subprocess.run(cmd, check=True)
print("wrote", out)
