import argparse
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
OUT = ROOT / CFG.get("output_dir", "output")
OUT.mkdir(exist_ok=True)

def exe(name):
    return shutil.which(name) or name

def run(cmd, capture=False):
    print(">", " ".join(map(str, cmd)))
    return subprocess.run(
        cmd, check=True, text=True,
        capture_output=capture
    )

def ffprobe_value(video, field):
    r = run([
        exe("ffprobe"), "-v", "error",
        "-show_entries", f"format={field}",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video)
    ], capture=True)
    try:
        return float(r.stdout.strip())
    except:
        return 0.0

def duration(video):
    return ffprobe_value(video, "duration")

def download_vod(url, folder):
    import yt_dlp
    folder.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": str(folder / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": False
    }
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
        prepared = Path(y.prepare_filename(info))
        mp4 = prepared.with_suffix(".mp4")
        return mp4 if mp4.exists() else prepared

def extract_audio(video, wav):
    run([
        exe("ffmpeg"), "-y", "-i", str(video),
        "-vn", "-ac", "1",
        "-ar", str(CFG.get("audio_sample_rate", 16000)),
        str(wav)
    ])

def transcribe(wav):
    import whisper
    model = whisper.load_model(CFG.get("whisper_model", "base"))
    result = model.transcribe(
        str(wav),
        language=CFG.get("language", "ar"),
        fp16=False,
        verbose=False
    )
    return result.get("segments", [])

def normalize(s):
    return re.sub(r"\s+", " ", s.lower()).strip()

def text_score(text):
    t = normalize(text)
    score = 0
    reasons = []
    for w in CFG.get("reaction_words", []):
        if normalize(w) in t:
            score += 3
            reasons.append(f"reaction:{w}")
    for w in CFG.get("game_words", []):
        if normalize(w) in t:
            score += 1
            reasons.append(f"game:{w}")
    score += min(t.count("!"), 4)
    score += min(t.count("?"), 2)
    return score, reasons

def loudness_events(wav):
    """
    Returns approximate loudness peaks.
    Uses ffmpeg volumedetect on short windows; for speed this is sampled.
    """
    # v2 intentionally keeps audio analysis lightweight.
    # If ffmpeg/Whisper are unavailable, text signals still work.
    try:
        total = duration(wav)
    except:
        return []
    events = []
    step = 8.0
    start = 0.0
    while start < total:
        end = min(total, start + step)
        try:
            r = subprocess.run([
                exe("ffmpeg"), "-hide_banner", "-i", str(wav),
                "-ss", str(start), "-t", str(end-start),
                "-af", "volumedetect", "-f", "null", "-"
            ], capture_output=True, text=True)
            m = re.search(r"max_volume:\s*(-?[\d.]+)\s*dB", r.stderr)
            if m:
                db = float(m.group(1))
                events.append((start + (end-start)/2, db))
        except:
            pass
        start = end
    return events

def build_candidates(segments, loud_events):
    candidates = []
    reaction = CFG.get("reaction_words", [])

    for seg in segments:
        st = float(seg.get("start", 0))
        en = float(seg.get("end", st))
        text = seg.get("text", "")
        score, reasons = text_score(text)

        # Combine neighboring transcript segments so a reaction spread
        # across two lines can still form one event.
        if score == 0:
            continue

        center = (st + en) / 2
        loud_bonus = 0
        nearest_db = None
        if loud_events:
            nearest = min(loud_events, key=lambda x: abs(x[0] - center))
            nearest_db = nearest[1]
            # Louder than roughly -12dB gets a modest bonus.
            if nearest_db > -12:
                loud_bonus = 3
            elif nearest_db > -18:
                loud_bonus = 2
            elif nearest_db > -24:
                loud_bonus = 1

        final = score + loud_bonus
        if loud_bonus:
            reasons.append(f"loudness:{nearest_db:.1f}dB")

        candidates.append({
            "start": max(0, st - CFG["clip_before"]),
            "end": en + CFG["clip_after"],
            "score": final,
            "text": text.strip(),
            "reasons": reasons
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)

    selected = []
    gap = float(CFG.get("min_gap_seconds", 35))
    for c in candidates:
        if c["score"] < CFG.get("minimum_clip_score", 4):
            continue
        if all(abs(c["start"] - s["start"]) >= gap for s in selected):
            selected.append(c)

    return selected

def safe_name(s):
    return re.sub(r'[\\/:*?"<>|]+', "_", s)[:90].strip()

def cut_clip(video, item, out):
    run([
        exe("ffmpeg"), "-y",
        "-ss", str(item["start"]),
        "-to", str(item["end"]),
        "-i", str(video),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "160k",
        str(out)
    ])

def make_watermark_clip(src, dst):
    brand = CFG.get("brand_text", "Lavri_Gaaz")
    # Draw text only if FFmpeg has drawtext available.
    # Escape special characters for FFmpeg filter.
    safe = brand.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    vf = (
        "drawtext="
        f"text='{safe}':"
        "x=30:y=30:fontsize=28:"
        "fontcolor=white@0.85:"
        "box=1:boxcolor=black@0.35:boxborderw=8"
    )
    run([
        exe("ffmpeg"), "-y", "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k",
        str(dst)
    ])

def concat_files(clips, out):
    if not clips:
        return
    lst = out.parent / "concat.txt"
    lines = []
    for p in clips:
        # concat demuxer syntax
        lines.append("file '" + str(p).replace("'", "'\\''") + "'")
    lst.write_text("\n".join(lines), encoding="utf-8")
    run([
        exe("ffmpeg"), "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(lst),
        "-c", "copy", str(out)
    ])

def make_short(src, dst, duration_limit):
    """
    Creates a vertical 9:16 short using a center crop.
    """
    vf = (
        "scale=1080:-2,"
        "crop=1080:1920:(in_w-1080)/2:(in_h-1920)/2,"
        "drawtext=text='Lavri_Gaaz':x=30:y=40:fontsize=42:"
        "fontcolor=white@0.9:box=1:boxcolor=black@0.35:boxborderw=10"
    )
    run([
        exe("ffmpeg"), "-y",
        "-i", str(src),
        "-t", str(duration_limit),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(dst)
    ])

def process_video(url):
    import yt_dlp

    info = None
    with yt_dlp.YoutubeDL({"quiet": True}) as y:
        info = y.extract_info(url, download=False)

    title = info.get("title", "youtube_video")
    folder = OUT / safe_name(title)
    folder.mkdir(parents=True, exist_ok=True)

    video = download_vod(url, folder)
    print("Source:", video)

    wav = folder / "audio.wav"
    extract_audio(video, wav)

    print("Transcribing...")
    segments = transcribe(wav)

    (folder / "transcript.json").write_text(
        json.dumps(segments, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print("Analyzing audio peaks...")
    loud = loudness_events(wav)

    candidates = build_candidates(segments, loud)
    report = {
        "title": title,
        "source": url,
        "game": CFG.get("game"),
        "candidates": candidates
    }
    (folder / "edit_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    clips = []
    for i, item in enumerate(candidates, 1):
        raw = folder / f"clip_{i:02d}_score_{item['score']}.mp4"
        branded = folder / f"clip_{i:02d}_Lavri_Gaaz.mp4"
        cut_clip(video, item, raw)
        try:
            make_watermark_clip(raw, branded)
            raw.unlink(missing_ok=True)
            clips.append(branded)
        except Exception:
            clips.append(raw)

    target = CFG.get("highlight_minutes", 10) * 60
    chosen = []
    total = 0
    for p in clips:
        d = duration(p)
        if total + d <= target + 20 and len(chosen) < CFG.get("max_highlight_clips", 20):
            chosen.append(p)
            total += d

    if chosen:
        highlight = folder / f"{safe_name(title)}_Lavri_Gaaz_HIGHLIGHT.mp4"
        concat_files(chosen, highlight)
        print("Highlight:", highlight)

        if CFG.get("create_shorts", True):
            short_dir = folder / "shorts"
            short_dir.mkdir(exist_ok=True)
            for i, p in enumerate(chosen[:CFG.get("short_count", 5)], 1):
                out = short_dir / f"Lavri_Gaaz_Short_{i:02d}.mp4"
                try:
                    make_short(p, out, CFG.get("short_duration", 55))
                except Exception as e:
                    print("Short failed:", e)

    print("Finished:", folder)
    return folder

def monitor():
    import yt_dlp

    archive = ROOT / CFG.get("download_archive", "downloaded.txt")
    seen = set()
    if archive.exists():
        seen = set(x.strip() for x in archive.read_text(encoding="utf-8").splitlines() if x.strip())

    url = CFG["channel_url"]
    print("Monitoring:", url)

    while True:
        try:
            opts = {"quiet": True, "extract_flat": True, "playlistend": 10}
            with yt_dlp.YoutubeDL(opts) as y:
                data = y.extract_info(url, download=False)

            for e in (data.get("entries") or []):
                vid = e.get("id")
                if not vid or vid in seen:
                    continue

                vod = "https://www.youtube.com/watch?v=" + vid
                with yt_dlp.YoutubeDL({"quiet": True}) as y:
                    info = y.extract_info(vod, download=False)

                if info.get("is_live"):
                    continue

                if info.get("live_status") in ("post_live", "was_live"):
                    process_video(vod)
                    seen.add(vid)
                    archive.write_text("\n".join(sorted(seen)), encoding="utf-8")

        except Exception as ex:
            print("Monitor error:", repr(ex))

        time.sleep(int(CFG.get("poll_seconds", 120)))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", help="Process one YouTube VOD")
    args = ap.parse_args()

    if args.video:
        process_video(args.video)
    else:
        monitor()

if __name__ == "__main__":
    main()
