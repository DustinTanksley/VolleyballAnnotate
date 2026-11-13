# Volleyball Score Annotator v0.5

Updated from v0.4.1. **Existing project JSON files are compatible.** The app stores score annotations in original-video timestamps. No video source files are changed.

## Setup (Windows)

From the extracted `volleyball_annotator` folder:

```powershell
py -m pip install -r requirements.txt
py app.py
```

FFmpeg and FFprobe must either be installed on PATH or placed next to `app.py` as `ffmpeg.exe` and `ffprobe.exe`. The built-in logos are included and embedded into newly created project JSONs. You can load your previous JSON files; please make a backup before editing them in a new version.

## New: Export an individual set (recommended)

1. Open your existing project and video, select Set 1–5 in **Set video ranges**.
2. Seek to the first frame you want included; press **Mark start**.
3. Seek to the last frame; press **Mark end**. You can edit both timestamp fields directly. Press **Save range** (marking also saves automatically).
4. Choose **1080p** for a good speed/size tradeoff when the original is 4K, **720p** for fast previews, or **Original resolution** for maximum detail.
5. Click **Export selected set**. The set is trimmed to exactly those source-video times, skipping warmups and breaks. The scoreboard is displayed at all times and changes when points are marked. New set ranges are saved inside the same JSON project.
6. Repeat for each set. Exporting one set does not alter event timestamps or replay annotations.

The new set exporter avoids re-encoding footage a second time simply to add the title card: it attempts a compatible stream-copy join (and has a re-encode fallback if needed). **Include 4-second title** is off by default for fastest test clips.

**Export video** still exports the entire recording, but now uses the same persistent score style. When the full-match exporter includes replays, it retains the older multi-pass rendering approach. For faster exports, prefer **Export selected set**.

Score and replay events remain indexed to the original video. For a per-set export, only replays whose *insertion point* lies inside that set are included. A replay can draw from source footage elsewhere in the recording.

## New: Persistent scoreboard

- Instead of dozens of opacity-transition frames per point, the renderer makes **one transparent PNG per new score**, holds it until the next change, and overlays it during encoding.
- The 15-second duration and fade settings are retained in older JSON data for compatibility but **are not used in v0.5 exports**.
- Placement defaults to bottom-left. Team logos remain embedded in project data.
- Scaling to 1080p happens *before* overlay blending, so the scoreboard remains readable at the exported resolution.

## New: Slower replays

The replay speed list now includes **0.05×, 0.1×, 0.125×, and 0.2×**, in addition to the previous 0.25×, 0.5×, 0.75×, and 1×. Previous replay records still work. Slower playback extends video duration: e.g., 2 seconds at 0.1× yields 20 seconds of replay. Crop position stays clamped inside the original frame. Replays are muted.

## New: Reliable export progress

The GUI now **incrementally reads the render log from where it left off**, tracking `STAGE|...`, `out_time`, and `speed` without losing earlier stage labels when the log exceeds 16 KB. The earlier issue with apparent progress freezes near 4% was due to relying on just the last 16 KB. The status bar reports the current stage, its percent, speed, and warns when no new frames have appeared for 15 seconds.

The `.render.log` file is still available next to each exported MP4. The progress bar measures **the current stage**, not the entire export, and resets for each additional stage (replay clip, title, etc.).

## Existing scoring workflow

- `A` scores Team A; `L` scores Team B. Enable **Pause when a point is marked** for automatic pausing, or leave disabled for continuous scoring.
- `N` marks an end-of-set event and prompts you to select a winner (the leading team is selected by default). No standard point thresholds are required. This is a *score event*, not a video trim marker: set video ranges must be explicitly marked separately.
- `Z` undo, Space playback, arrows seek. Select an event to move its timestamp or change a team; JSON autosaves.
- Replays: Mark IN, Mark OUT, Set insert, choose crop, choose playback speed, then Add replay. Select a replay to edit later.

## Command-line use

```powershell
py renderer.py match.json set_1.mp4 --set 1 --height 1080 --encoder auto --no-title
py renderer.py match.json set_2.mp4 --set 2 --height 0 --encoder nvenc --no-title
py renderer.py match.json whole_match.mp4 --encoder auto
```

`--height` accepts `0` (source resolution), `720`, `1080`, or `2160` (clamped to the input height). `--no-title` prevents the optional 4-second title card. Use `--set` only for marked sets.

## Performance details and limitations

Your supplied example log has a **3840×2160, ~30 fps, 48 Mbps** input. It confirms NVENC selected, but the original scoreboard pass ran at ~2.4×. The old complete export also re-encoded regular segments around replays and encoded the complete assembled movie again after prepending the title. That's why a hardware encoder alone did not lead to quick turnaround.

The per-set exporter trims first, optionally scales 4K to 1080p, and renders the persistent scoreboard in a single pass. With replays, the current replay processor still re-encodes the base portions between replay insertions; optimization of that more complex path is a future improvement. FFmpeg 4K decoding, CPU scaling, overlay blending, and disk I/O can remain bottlenecks even with NVENC.

**Testing:** FFmpeg renders have been tested on synthetic H.264/AAC input, including per-set trimming, scoreboard, replay insertion at 0.1×, and optional title-card stream-copy joining. The PySide6 Windows GUI has been syntax checked but not run on a Windows desktop in this environment, and RTX 4090 performance has not been benchmarked here.

## v0.5.1 — Replay timestamp fix for per-set export

Fixed a crash when exporting a set containing instant replays located later in the original recording. Replay source IN/OUT timestamps now validate against the full source duration, while insertion times are converted into set-relative positions only when assembling the trimmed export. Existing JSON projects do not need migration. Whole-video export behavior is unchanged.
