import os
import time
import uuid
import subprocess
import re
from pathlib import Path
from threading import Thread, Semaphore
from collections import defaultdict

import yt_dlp
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

from fastapi.responses import JSONResponse

@app.exception_handler(Exception)
async def catch_all_errors(request: Request, exc: Exception):
    """Safety net: if anything unexpected breaks, tell the user to try again later
    instead of leaving them stuck waiting with no response."""
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on our end. Please try again in a moment."}
    )

DOWNLOADS = Path("downloads")
DOWNLOADS.mkdir(exist_ok=True)

# ── LIMITS ───────────────────────────────
MAX_DURATION_SECONDS = 10 * 60      # reject videos longer than 10 minutes
RATE_LIMIT_MAX = 5                  # max requests
RATE_LIMIT_WINDOW = 60              # per this many seconds, per IP
YTDLP_TIMEOUT = 60                  # give up on a stuck/slow link after this many seconds
MAX_CONCURRENT_DOWNLOADS = 2        # only this many /pull downloads run at once, rest wait

ALLOWED_DOMAINS = [
    "youtube.com", "youtu.be",
    "tiktok.com",
    "instagram.com",
    "twitter.com", "x.com",
    "facebook.com", "fb.watch",
    "reddit.com",
]

def is_allowed_url(url: str) -> bool:
    url = url.lower()
    return any(d in url for d in ALLOWED_DOMAINS)

download_slots = Semaphore(MAX_CONCURRENT_DOWNLOADS)

# ── PROGRESS TRACKING ───────────────────
conversion_progress = {}

# ── RATE LIMITING (simple in-memory) ────
request_log = defaultdict(list)

def check_rate_limit(request: Request):
    ip = request.client.host
    now = time.time()
    request_log[ip] = [t for t in request_log[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(request_log[ip]) >= RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment and try again.")
    request_log[ip].append(now)

# ── MODELS ──────────────────────────────
class PullRequest(BaseModel):
    url: str
    quality: str = None
    format_id: str = None
    is_audio: bool = False

class UrlRequest(BaseModel):
    url: str

# ── HELPERS ─────────────────────────────
def quality_to_format(quality: str) -> str:
    return {
        "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "720p":  "bestvideo[height<=720]+bestaudio/best[height<=720]",
        "480p":  "bestvideo[height<=480]+bestaudio/best[height<=480]",
        "audio": "bestaudio/best",
    }.get(quality, "best")

def get_video_duration(file_path: str) -> float:
    """Get video duration in seconds using ffprobe"""
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1:noprint_wrappers=1", str(file_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return float(result.stdout.strip())
    except:
        return 0

def parse_ffmpeg_time(time_str: str) -> float:
    """Convert HH:MM:SS.ms to seconds"""
    try:
        parts = time_str.split(":")
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except:
        return 0

def cleanup_old_files():
    """Safety net: delete any leftover files older than 15 minutes (e.g. user never clicked download)."""
    while True:
        time.sleep(300)  # check every 5 minutes
        cutoff = time.time() - (15 * 60)
        for f in DOWNLOADS.glob("*"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
            except:
                pass

Thread(target=cleanup_old_files, daemon=True).start()

# ── API ROUTES ───────────────────────────
@app.post("/check")
def check_formats(req: UrlRequest, request: Request):
    """Look at the link and return the real formats it actually has (no download)."""
    check_rate_limit(request)
    if not is_allowed_url(req.url):
        raise HTTPException(status_code=400, detail="This site isn't supported yet. Try a YouTube, TikTok, Instagram, Twitter/X, Facebook, or Reddit link.")
    ydl_opts = {"quiet": True, "noplaylist": True, "skip_download": True, "socket_timeout": YTDLP_TIMEOUT}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    duration = info.get("duration") or 0
    if duration > MAX_DURATION_SECONDS:
        raise HTTPException(status_code=400, detail=f"Video is too long (max {MAX_DURATION_SECONDS//60} minutes for now).")

    formats = []
    seen = set()
    for f in info.get("formats", []):
        height = f.get("height")
        ext = f.get("ext")
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")

        # Skip formats with no video and no audio (e.g. storyboard thumbnails)
        if vcodec == "none" and acodec == "none":
            continue

        if vcodec != "none" and height:
            # Video format — cap at 1080p to keep file sizes manageable on free hosting
            if height > 1080:
                continue
            label = f"{height}p"
            key = (label, ext)
            if key in seen:
                continue
            seen.add(key)
            formats.append({
                "format_id": f.get("format_id"),
                "type": "video",
                "resolution": label,
                "ext": ext,
                "height": height,
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "label": f"{label} · {ext.upper()}",
            })
        elif vcodec == "none" and acodec != "none":
            # Audio-only format
            abr = f.get("abr")
            key = ("audio", ext)
            if key in seen:
                continue
            seen.add(key)
            formats.append({
                "format_id": f.get("format_id"),
                "type": "audio",
                "resolution": "audio",
                "ext": ext,
                "height": 0,
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "label": f"Audio · {ext.upper()}" + (f" · {int(abr)}kbps" if abr else ""),
            })

    # Sort: video formats highest resolution first, then audio
    formats.sort(key=lambda x: (-x["height"]))

    return {
        "success": True,
        "title": info.get("title", "video"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "formats": formats,
    }

@app.post("/pull")
def pull_video(req: PullRequest, request: Request):
    check_rate_limit(request)
    if not is_allowed_url(req.url):
        raise HTTPException(status_code=400, detail="This site isn't supported yet. Try a YouTube, TikTok, Instagram, Twitter/X, Facebook, or Reddit link.")
    output_id = str(uuid.uuid4())
    output_path = DOWNLOADS / output_id

    # Preferred path: user picked an exact format_id from /check results
    if req.format_id:
        ydl_opts = {
            "format": req.format_id,
            "outtmpl": str(output_path) + ".%(ext)s",
            "noplaylist": True, "quiet": True, "socket_timeout": YTDLP_TIMEOUT,
        }
        if req.is_audio:
            ydl_opts["postprocessors"] = [{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"192"}]
    else:
        # Fallback: old fixed-quality behavior, kept for backwards compatibility
        ydl_opts = {
            "format": quality_to_format(req.quality or "best"),
            "outtmpl": str(output_path) + ".%(ext)s",
            "noplaylist": True, "quiet": True, "socket_timeout": YTDLP_TIMEOUT,
        }
        if req.quality == "audio":
            ydl_opts["postprocessors"] = [{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"192"}]

    # Wait for a free download slot — protects the small free server from being
    # overwhelmed if many people click download at the same moment
    got_slot = download_slots.acquire(timeout=YTDLP_TIMEOUT)
    if not got_slot:
        raise HTTPException(status_code=503, detail="Server is busy right now. Please try again in a moment.")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=True)
            title = info.get("title", "video")
            ext = "mp3" if (req.is_audio or req.quality == "audio") else info.get("ext", "mp4")
            final_file = str(output_path) + f".{ext}"
        return {"success":True,"filename":os.path.basename(final_file),"title":title,"download_url":f"/download/{os.path.basename(final_file)}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        download_slots.release()

@app.post("/to-audio")
async def to_audio(file: UploadFile = File(...), output_format: str = "mp3"):
    input_id = str(uuid.uuid4())
    input_path = DOWNLOADS / f"{input_id}{Path(file.filename).suffix}"
    output_path = DOWNLOADS / f"{input_id}.{output_format}"
    with open(input_path, "wb") as f:
        f.write(await file.read())
    result = subprocess.run(["ffmpeg","-y","-i",str(input_path),"-q:a","0","-map","a",str(output_path)], capture_output=True)
    input_path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail="FFmpeg error")
    return {"success":True,"filename":output_path.name,"download_url":f"/download/{output_path.name}","suggested_name":f"{Path(file.filename).stem}.{output_format}"}

@app.post("/convert")
async def convert_file(file: UploadFile = File(...), output_format: str = "mp4"):
    input_id = str(uuid.uuid4())
    input_path = DOWNLOADS / f"{input_id}{Path(file.filename).suffix}"
    output_path = DOWNLOADS / f"{input_id}.{output_format.lower()}"
    
    # Initialize progress tracking
    conversion_progress[input_id] = {"status": "uploading", "percentage": 0}
    print(f"[{input_id}] 📤 UPLOADING: {file.filename}")
    
    with open(input_path, "wb") as f:
        f.write(await file.read())
    
    print(f"[{input_id}] ✅ Upload complete")
    conversion_progress[input_id] = {"status": "analyzing", "percentage": 5}
    
    # Get video duration for progress calculation
    print(f"[{input_id}] 🔍 Analyzing video...")
    duration = get_video_duration(str(input_path))
    print(f"[{input_id}] Duration: {duration:.2f}s")
    
    audio_formats = {"mp3","m4a","wav","ogg","aac","flac"}
    fmt = output_format.lower()
    
    # Build FFmpeg command
    if fmt in audio_formats:
        cmd = ["ffmpeg","-y","-i",str(input_path),"-q:a","0","-map","a",str(output_path)]
        print(f"[{input_id}] 🎵 Converting to {fmt.upper()} audio format")
    elif fmt == "mp4":
        cmd = ["ffmpeg","-y","-i",str(input_path),"-c:v","libx264","-preset","medium","-crf","23","-c:a","aac","-b:a","128k","-progress","pipe:1",str(output_path)]
        print(f"[{input_id}] 🎬 Converting to MP4 video (H.264)")
    else:
        cmd = ["ffmpeg","-y","-i",str(input_path),"-progress","pipe:1",str(output_path)]
        print(f"[{input_id}] 🎥 Converting to {fmt.upper()}")
    
    print(f"[{input_id}] ⏳ Starting conversion...")
    conversion_progress[input_id] = {"status": "converting", "percentage": 10}
    
    # Run FFmpeg and track progress
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        
        # Read stdout in separate thread to avoid deadlock
        import threading
        stderr_output = []
        
        def read_stderr():
            for line in process.stderr:
                stderr_output.append(line)
                if "error" in line.lower():
                    print(f"[{input_id}] ⚠️ FFmpeg: {line.strip()}")
        
        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        stderr_thread.start()
        
        last_percentage = 10
        for line in process.stdout:
            if line.startswith("out_time_ms="):
                try:
                    time_ms = int(line.split("=")[1])
                    if duration > 0:
                        percentage = min(95, int((time_ms / 1000000) / duration * 100))
                        if percentage > last_percentage:
                            conversion_progress[input_id] = {"status": "converting", "percentage": percentage}
                            print(f"[{input_id}] Progress: {percentage}%")
                            last_percentage = percentage
                except:
                    pass
        
        process.wait(timeout=3600)  # 1 hour timeout for large files
        stderr_thread.join(timeout=5)
        
        if process.returncode != 0:
            error_msg = "".join(stderr_output[-20:]) if stderr_output else "FFmpeg conversion failed"
            conversion_progress[input_id] = {"status": "failed", "percentage": 0, "error": error_msg}
            print(f"[{input_id}] ❌ FAILED (return code {process.returncode}): {error_msg[:200]}")
            input_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=str(error_msg)[:200])
        
        print(f"[{input_id}] ✅ Conversion completed 100%")
        conversion_progress[input_id] = {"status": "completed", "percentage": 100}
        input_path.unlink(missing_ok=True)
        
        return {"success":True,"conversion_id":input_id,"filename":output_path.name,"download_url":f"/download/{output_path.name}","suggested_name":f"{Path(file.filename).stem}.{fmt}"}
    
    except subprocess.TimeoutExpired:
        print(f"[{input_id}] ❌ TIMEOUT: Conversion took too long")
        conversion_progress[input_id] = {"status": "failed", "percentage": 0, "error": "Conversion timeout"}
        input_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Conversion timeout - file too large or system too slow")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[{input_id}] ❌ ERROR: {str(e)}")
        conversion_progress[input_id] = {"status": "failed", "percentage": 0, "error": str(e)}
        input_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/convert-progress/{conversion_id}")
def get_conversion_progress(conversion_id: str):
    """Get current conversion progress"""
    progress = conversion_progress.get(conversion_id, {"status": "unknown", "percentage": 0})
    return progress

@app.get("/download/{filename}")
def download_file(filename: str):
    # Basic safety: don't allow path tricks like ../
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    file_path = DOWNLOADS / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    # Delete the file right after it finishes sending — nothing sits on disk
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/octet-stream",
        background=BackgroundTask(lambda: file_path.unlink(missing_ok=True))
    )

@app.delete("/cleanup/{filename}")
def cleanup(filename: str):
    (DOWNLOADS / filename).unlink(missing_ok=True)
    return {"success": True}

# ── SERVE REACT UI (must be last) ────────
DIST = Path(__file__).parent / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="static")