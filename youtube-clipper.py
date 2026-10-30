"""
ytclip.py - download a YouTube video (or just a section of it) as MP4.

Usage:
    python ytclip.py                       # interactive prompts
    python ytclip.py URL                   # full video
    python ytclip.py URL -s 1:30 -e 2:45   # only 1:30 to 2:45
    python ytclip.py URL -s 90 -q 720      # from 90 seconds to the end, max 720p
    python ytclip.py URL -o ~/Videos       # choose output folder

Times accept: SS, MM:SS or HH:MM:SS (decimals ok, e.g. 1:30.5)
Requires: py -m pip install -U yt-dlp imageio-ffmpeg
Run with: py youtubetomp4.py
"""

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

try:
    import yt_dlp
    from yt_dlp.utils import download_range_func
except ImportError:
    sys.exit("yt-dlp is not installed. Run:  py -m pip install -U yt-dlp")

NUM = re.compile(r"\d+(\.\d+)?")


def parse_time(text):
    """'1:30' -> 90.0, '1:02:03' -> 3723.0, '' -> None."""
    text = (text or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3 or not all(NUM.fullmatch(p) for p in parts):
        raise ValueError(f"Invalid time '{text}'. Use SS, MM:SS or HH:MM:SS.")
    secs = 0.0
    for p in parts:
        secs = secs * 60 + float(p)
    return secs


def fmt_time(secs):
    secs = int(round(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def ask(prompt):
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        sys.exit("\nCancelled.")


def main():
    ap = argparse.ArgumentParser(description="Download a YouTube video/clip as MP4.")
    ap.add_argument("url", nargs="?", help="YouTube link")
    ap.add_argument("-s", "--start", help="start time (blank = beginning)")
    ap.add_argument("-e", "--end", help="end time (blank = end of video)")
    ap.add_argument("-q", "--quality", type=int, default=1080,
                    help="max height in pixels (default 1080; e.g. 720, 480)")
    ap.add_argument("-o", "--out", default=".", help="output folder (default: current)")
    args = ap.parse_args()

    # Find ffmpeg: use the one on PATH if present, otherwise the pip-bundled copy.
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        try:
            import imageio_ffmpeg
            bundled = Path(imageio_ffmpeg.get_ffmpeg_exe())
            # yt-dlp only recognises a binary literally named ffmpeg(.exe),
            # so copy the bundled one to a plain-named file.
            local = Path.home() / ".ytclip" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
            if not local.exists():
                local.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bundled, local)
                local.chmod(0o755)
            ffmpeg_path = str(local)
        except ImportError:
            sys.exit("ffmpeg not found. Run:  py -m pip install imageio-ffmpeg")

    # yt-dlp's check for partial downloads only looks at PATH (it ignores
    # ffmpeg_location), so put ffmpeg's folder on PATH for this run.
    os.environ["PATH"] = str(Path(ffmpeg_path).parent) + os.pathsep + os.environ.get("PATH", "")

    interactive = args.url is None
    url = args.url or ask("YouTube link: ")
    if not url:
        sys.exit("No link given.")

    # Look up the video first so we can show the title/duration and validate times.
    probe_opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(probe_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        sys.exit(f"Could not fetch video: {e}")

    if info.get("is_live"):
        sys.exit("This is a live stream; it can't be clipped until it ends.")

    duration = info.get("duration")
    print(f"\nTitle   : {info.get('title')}")
    print(f"Length  : {fmt_time(duration) if duration else 'unknown'}\n")

    start_raw, end_raw = args.start, args.end
    if interactive and start_raw is None and end_raw is None:
        print("Press Enter to skip a timestamp (start = beginning, end = end).")
        start_raw = ask("Start time (e.g. 1:30): ")
        end_raw = ask("End time   (e.g. 2:45): ")

    try:
        start = parse_time(start_raw)
        end = parse_time(end_raw)
    except ValueError as e:
        sys.exit(str(e))

    # Validate the range.
    if start is not None and duration and start >= duration:
        sys.exit(f"Start ({fmt_time(start)}) is past the end of the video ({fmt_time(duration)}).")
    if end is not None and duration and end > duration:
        print(f"Note: end time is past the video length; trimming to {fmt_time(duration)}.")
        end = duration
    if start is not None and end is not None and end <= start:
        sys.exit("End time must be after start time.")

    clipping = start is not None or end is not None
    s_val = start if start is not None else 0
    e_val = end if end is not None else (duration or float("inf"))

    # Filename suffix like " [01-30 to 02-45]" (no colons, safe on Windows).
    suffix = ""
    if clipping:
        suffix = f" [{fmt_time(s_val).replace(':', '-')} to " \
                 f"{fmt_time(e_val).replace(':', '-') if e_val != float('inf') else 'end'}]"

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    h = args.quality
    ydl_opts = {
        # Fetch the best video and best audio up to the requested height, or the best pre-merged format
        "format": f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best",
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / f"%(title).120s{suffix}.%(ext)s"),
        "noplaylist": True,
        "restrictfilenames": False,
        "windowsfilenames": True,
        "ffmpeg_location": ffmpeg_path,
    }
    if clipping:
        ydl_opts["download_ranges"] = download_range_func(None, [(s_val, e_val)])
        # Re-encode at the cut points so the clip starts exactly where you asked.
        ydl_opts["force_keyframes_at_cuts"] = True

    print("Downloading..." + (f" clip {fmt_time(s_val)} -> "
                              f"{fmt_time(e_val) if e_val != float('inf') else 'end'}" if clipping else ""))
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as e:
        sys.exit(f"Download failed: {e}")

    print(f"\nDone! Saved in: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
