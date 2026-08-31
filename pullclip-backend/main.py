
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from threading import Thread, Semaphore, Lock
from collections import defaultdict
from urllib.parse import urlparse

import yt_dlp

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="PullClip API",
    version="1.2.0",
)


# ============================================================
# CORS
# ============================================================

# Your Vercel frontend.
#
# IMPORTANT:
# Keep the trailing slash OUT of the origin.
FRONTEND_ORIGINS = [
    "https://pullclips-nu.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


# ============================================================
# SECURITY HEADERS
# ============================================================

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=()"
    )

    return response


# ============================================================
# STORAGE
# ============================================================

DOWNLOADS = Path("downloads")

DOWNLOADS.mkdir(
    parents=True,
    exist_ok=True,
)


# ============================================================
# LIMITS
# ============================================================

MAX_DURATION_SECONDS = 10 * 60
MAX_VIDEO_HEIGHT = 1080

YTDLP_TIMEOUT = 60

MAX_CONCURRENT_DOWNLOADS = 1

CLEANUP_AFTER_SECONDS = 15 * 60

MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024

MAX_REQUEST_BODY_BYTES = 10 * 1024

RATE_LIMIT_MAX = 3
RATE_LIMIT_WINDOW = 60


# ============================================================
# DENO
# ============================================================

DENO_CANDIDATES = [
    "/opt/render/.deno/bin/deno",
    "/opt/render/project/src/.deno/bin/deno",
    str(Path.cwd() / ".deno" / "bin" / "deno"),
]

DENO_PATH = None

for candidate in DENO_CANDIDATES:
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        DENO_PATH = candidate
        break

print("")
print("============================================")
print("       PULLCLIP ENVIRONMENT CHECK")
print("============================================")
print("Deno PATH:", DENO_PATH)
print("System PATH:", os.environ.get("PATH"))

if DENO_PATH:
    try:
        result = subprocess.run(
            [
                DENO_PATH,
                "--version",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print("")
        print("Deno version:")
        print(result.stdout.strip())

        if result.stderr:
            print("")
            print("Deno stderr:")
            print(result.stderr.strip())

    except Exception as e:
        print("")
        print("❌ Deno check failed:")
        print(e)

else:
    print("")
    print("❌ DENO NOT FOUND")
    print("Checked these locations:")

    for candidate in DENO_CANDIDATES:
        print(f"  - {candidate}")

print("============================================")
print("")


# ============================================================
# SUPPORTED SITES
# ============================================================

ALLOWED_DOMAINS = {
    "youtube.com",
    "youtu.be",
    "tiktok.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "fb.watch",
    "reddit.com",
}


# ============================================================
# RATE LIMIT STORAGE
# ============================================================

request_log = defaultdict(list)

rate_limit_lock = Lock()


# ============================================================
# DOWNLOAD CONCURRENCY
# ============================================================

download_slots = Semaphore(
    MAX_CONCURRENT_DOWNLOADS
)


# ============================================================
# REQUEST MODELS
# ============================================================

class UrlRequest(BaseModel):
    url: str


class PullRequest(BaseModel):
    url: str
    quality: str | None = None
    format_id: str | None = None
    is_audio: bool = False


# ============================================================
# REQUEST BODY SIZE
# ============================================================

@app.middleware("http")
async def request_size_limit(
    request: Request,
    call_next,
):
    content_length = request.headers.get(
        "content-length"
    )

    if content_length:
        try:
            content_length = int(content_length)

            if content_length > MAX_REQUEST_BODY_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="Request is too large.",
                )

        except ValueError:
            pass

    return await call_next(request)


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "pullclip",
        "deno_available": DENO_PATH is not None,
    }


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "pullclip",
        "message": "PullClip API is running.",
    }


# ============================================================
# URL VALIDATION
# ============================================================

def is_allowed_url(url: str) -> bool:
    """
    Safely validate the actual hostname.
    """

    try:
        url = url.strip()

        if not url:
            return False

        parsed = urlparse(url)

        if parsed.scheme not in {
            "http",
            "https",
        }:
            return False

        hostname = parsed.hostname

        if not hostname:
            return False

        hostname = hostname.lower().rstrip(".")

        if hostname.startswith("www."):
            hostname = hostname[4:]

        for domain in ALLOWED_DOMAINS:

            if hostname == domain:
                return True

            if hostname.endswith("." + domain):
                return True

        return False

    except Exception:
        return False


# ============================================================
# RATE LIMIT
# ============================================================

def check_rate_limit(
    request: Request,
):
    """
    Simple in-memory rate limiter.

    This protects the current Render instance.
    """

    ip = (
        request.client.host
        if request.client
        else "unknown"
    )

    now = time.time()

    with rate_limit_lock:

        request_log[ip] = [
            timestamp
            for timestamp in request_log[ip]
            if now - timestamp < RATE_LIMIT_WINDOW
        ]

        if len(request_log[ip]) >= RATE_LIMIT_MAX:

            raise HTTPException(
                status_code=429,
                detail=(
                    "Too many requests. "
                    "Please wait a moment "
                    "and try again."
                ),
            )

        request_log[ip].append(now)


# ============================================================
# URL INPUT VALIDATION
# ============================================================

def validate_url_or_raise(
    url: str,
):

    if not url:

        raise HTTPException(
            status_code=400,
            detail="Please provide a video URL.",
        )

    if len(url) > 2048:

        raise HTTPException(
            status_code=400,
            detail="The URL is too long.",
        )

    if not is_allowed_url(url):

        raise HTTPException(
            status_code=400,
            detail=(
                "This site isn't supported yet. "
                "Try a YouTube, TikTok, Instagram, "
                "Twitter/X, Facebook, or Reddit link."
            ),
        )


# ============================================================
# FILE CLEANUP
# ============================================================

def delete_job_files(
    job_id: str,
):
    """
    Delete every file belonging to a job.
    """

    for file_path in DOWNLOADS.glob(
        f"{job_id}.*"
    ):

        try:

            if file_path.is_file():

                file_path.unlink(
                    missing_ok=True
                )

                print(
                    f"🗑️ Deleted: "
                    f"{file_path.name}"
                )

        except Exception as e:

            print(
                f"⚠️ Could not delete "
                f"{file_path}: {e}"
            )


# ============================================================
# AUTOMATIC CLEANUP
# ============================================================

def cleanup_old_files():

    """
    Every 5 minutes:
    delete files older than 15 minutes.
    """

    while True:

        try:

            time.sleep(300)

            cutoff = (
                time.time()
                - CLEANUP_AFTER_SECONDS
            )

            for file_path in DOWNLOADS.iterdir():

                try:

                    if not file_path.is_file():
                        continue

                    modified_time = (
                        file_path.stat().st_mtime
                    )

                    if modified_time < cutoff:

                        file_path.unlink(
                            missing_ok=True
                        )

                        print(
                            "🧹 Removed old "
                            f"file: "
                            f"{file_path.name}"
                        )

                except Exception as e:

                    print(
                        f"Cleanup error: {e}"
                    )

        except Exception as e:

            print(
                f"Cleanup worker error: {e}"
            )


Thread(
    target=cleanup_old_files,
    daemon=True,
).start()


# ============================================================
# YT-DLP OPTIONS
# ============================================================

def get_ytdlp_runtime_options():

    """
    Build the JavaScript runtime configuration.

    If Deno exists, tell yt-dlp to use it.
    """

    options = {}

    if DENO_PATH:

        options["js_runtimes"] = {
            "deno": {
                "path": DENO_PATH
            }
        }

    return options


# ============================================================
# QUALITY → YT-DLP FORMAT
# ============================================================

def quality_to_format(
    quality: str | None,
) -> str:

    quality = (
        quality or "best"
    ).lower()

    formats = {

        "1080p": (
            "bestvideo[height<=1080]"
            "+bestaudio/"
            "best[height<=1080]"
        ),

        "720p": (
            "bestvideo[height<=720]"
            "+bestaudio/"
            "best[height<=720]"
        ),

        "480p": (
            "bestvideo[height<=480]"
            "+bestaudio/"
            "best[height<=480]"
        ),

        "360p": (
            "bestvideo[height<=360]"
            "+bestaudio/"
            "best[height<=360]"
        ),

        "best": (
            "bestvideo[height<=1080]"
            "+bestaudio/"
            "best[height<=1080]"
        ),
    }

    return formats.get(
        quality,
        formats["best"],
    )


# ============================================================
# FIND FORMAT
# ============================================================

def get_requested_format(
    info: dict,
    format_id: str,
):

    for fmt in info.get(
        "formats",
        [],
    ):

        if str(
            fmt.get("format_id")
        ) == str(format_id):

            return fmt

    return None


# ============================================================
# FILE SIZE CHECK
# ============================================================

def validate_file_size(
    file_path: Path,
):

    try:

        size = file_path.stat().st_size

    except FileNotFoundError:

        raise RuntimeError(
            "Downloaded file disappeared."
        )

    if size > MAX_FILE_SIZE_BYTES:

        print(
            f"❌ File too large: "
            f"{size} bytes"
        )

        try:
            file_path.unlink(
                missing_ok=True
            )
        except Exception:
            pass

        raise HTTPException(
            status_code=413,
            detail=(
                "The downloaded file is too large. "
                "Maximum allowed size is 500 MB."
            ),
        )


# ============================================================
# CHECK URL / FORMATS
# ============================================================

@app.post("/check")
def check_formats(
    req: UrlRequest,
    request: Request,
):

    check_rate_limit(request)

    url = req.url.strip()

    validate_url_or_raise(url)

    ydl_opts = {
        "quiet": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": YTDLP_TIMEOUT,
    }

    ydl_opts.update(
        get_ytdlp_runtime_options()
    )

    try:

        with yt_dlp.YoutubeDL(
            ydl_opts
        ) as ydl:

            info = ydl.extract_info(
                url,
                download=False,
            )

    except Exception as e:

        error_text = str(e)

        print(
            f"❌ Check failed: "
            f"{error_text}"
        )

        if (
            "429" in error_text
            or "Too Many Requests" in error_text
        ):

            raise HTTPException(
                status_code=503,
                detail=(
                    "YouTube temporarily "
                    "rate-limited this server. "
                    "Please try again later."
                ),
            )

        if (
            "Sign in to confirm"
            in error_text
            or "not a bot"
            in error_text
        ):

            raise HTTPException(
                status_code=503,
                detail=(
                    "The source website requires "
                    "additional verification."
                ),
            )

        raise HTTPException(
            status_code=502,
            detail=(
                "Could not read this video. "
                "The link may be private, invalid, "
                "temporarily unavailable, or blocked "
                "by the source website."
            ),
        )

    duration = (
        info.get("duration")
        or 0
    )

    if duration > MAX_DURATION_SECONDS:

        raise HTTPException(
            status_code=400,
            detail=(
                "This video is too long. "
                "The maximum allowed length "
                "is 10 minutes."
            ),
        )

    formats = []

    seen = set()

    for fmt in info.get(
        "formats",
        [],
    ):

        height = fmt.get("height")
        ext = fmt.get("ext")
        vcodec = fmt.get("vcodec")
        acodec = fmt.get("acodec")
        format_id = fmt.get("format_id")

        if not format_id:
            continue

        # VIDEO
        if (
            vcodec
            and vcodec != "none"
            and height
        ):

            if height > MAX_VIDEO_HEIGHT:
                continue

            if height < 144:
                continue

            label = f"{height}p"

            key = (
                "video",
                height,
                ext,
            )

            if key in seen:
                continue

            seen.add(key)

            formats.append(
                {
                    "format_id": format_id,
                    "type": "video",
                    "resolution": label,
                    "height": height,
                    "ext": ext,
                    "filesize": (
                        fmt.get("filesize")
                        or fmt.get("filesize_approx")
                    ),
                    "label": (
                        f"{label} · "
                        f"{ext.upper()}"
                    ),
                }
            )

        # AUDIO
        elif (
            vcodec == "none"
            and acodec
            and acodec != "none"
        ):

            abr = fmt.get("abr")

            key = (
                "audio",
                ext,
            )

            if key in seen:
                continue

            seen.add(key)

            formats.append(
                {
                    "format_id": format_id,
                    "type": "audio",
                    "resolution": "audio",
                    "height": 0,
                    "ext": ext,
                    "filesize": (
                        fmt.get("filesize")
                        or fmt.get("filesize_approx")
                    ),
                    "label": (
                        f"Audio · "
                        f"{ext.upper()}"
                        + (
                            f" · {int(abr)}kbps"
                            if abr
                            else ""
                        )
                    ),
                }
            )

    formats.sort(
        key=lambda item: (
            item.get("height", 0),
            item.get("type") == "video",
        ),
        reverse=True,
    )

    return {
        "success": True,
        "title": info.get(
            "title",
            "video",
        ),
        "thumbnail": info.get(
            "thumbnail"
        ),
        "duration": duration,
        "formats": formats,
    }


# ============================================================
# DOWNLOAD / PULL
# ============================================================

@app.post("/pull")
def pull_video(
    req: PullRequest,
    request: Request,
):

    check_rate_limit(request)

    url = req.url.strip()

    validate_url_or_raise(url)

    job_id = str(
        uuid.uuid4()
    )

    output_template = (
        str(
            DOWNLOADS / job_id
        )
        + ".%(ext)s"
    )

    got_slot = (
        download_slots.acquire(
            timeout=YTDLP_TIMEOUT
        )
    )

    if not got_slot:

        raise HTTPException(
            status_code=503,
            detail=(
                "The server is busy right now. "
                "Please try again in a moment."
            ),
        )

    try:

        # ====================================================
        # GET INFO
        # ====================================================

        info_opts = {
            "quiet": True,
            "noplaylist": True,
            "skip_download": True,
            "socket_timeout": YTDLP_TIMEOUT,
        }

        info_opts.update(
            get_ytdlp_runtime_options()
        )

        with yt_dlp.YoutubeDL(
            info_opts
        ) as ydl:

            info = ydl.extract_info(
                url,
                download=False,
            )

        # ====================================================
        # DURATION
        # ====================================================

        duration = (
            info.get("duration")
            or 0
        )

        if duration > MAX_DURATION_SECONDS:

            raise HTTPException(
                status_code=400,
                detail=(
                    "This video is too long. "
                    "The maximum allowed length "
                    "is 10 minutes."
                ),
            )

        # ====================================================
        # BUILD FORMAT
        # ====================================================

        if req.format_id:

            selected_format = get_requested_format(
                info,
                req.format_id,
            )

            if selected_format is None:

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Invalid video format. "
                        "Please refresh the page "
                        "and try again."
                    ),
                )

            selected_height = selected_format.get(
                "height"
            )

            if (
                selected_height is not None
                and selected_height > MAX_VIDEO_HEIGHT
            ):

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "That video quality "
                        "is not supported. "
                        "Maximum quality is 1080p."
                    ),
                )

            if req.is_audio:

                if (
                    selected_format.get("vcodec")
                    != "none"
                ):

                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "The selected format "
                            "is not an audio format."
                        ),
                    )

                format_selector = req.format_id

            else:

                if (
                    selected_format.get("vcodec")
                    == "none"
                ):

                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "The selected format "
                            "is not a video format."
                        ),
                    )

                format_selector = (
                    f"{req.format_id}"
                    "+bestaudio/"
                    f"{req.format_id}"
                )

        else:

            format_selector = quality_to_format(
                req.quality
            )

        # ====================================================
        # YT-DLP OPTIONS
        # ====================================================

        ydl_opts = {
            "format": format_selector,
            "outtmpl": output_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": YTDLP_TIMEOUT,
            "merge_output_format": "mp4",
            "continuedl": False,
            "overwrites": False,
        }

        ydl_opts.update(
            get_ytdlp_runtime_options()
        )

        # ====================================================
        # DOWNLOAD
        # ====================================================

        print(
            f"⬇️ Starting download "
            f"[{job_id}]"
        )

        with yt_dlp.YoutubeDL(
            ydl_opts
        ) as ydl:

            downloaded_info = (
                ydl.extract_info(
                    url,
                    download=True,
                )
            )

        title = (
            downloaded_info.get(
                "title",
                "video",
            )
        )

        # ====================================================
        # FIND OUTPUT
        # ====================================================

        files = list(
            DOWNLOADS.glob(
                f"{job_id}.*"
            )
        )

        files = [
            file_path
            for file_path in files
            if (
                file_path.is_file()
                and not file_path.name.endswith(
                    ".part"
                )
                and not file_path.name.endswith(
                    ".ytdl"
                )
            )
        ]

        if not files:

            raise RuntimeError(
                "Download completed but "
                "the output file could "
                "not be found."
            )

        final_file = max(
            files,
            key=lambda path: path.stat().st_mtime,
        )

        # ====================================================
        # FILE SIZE SECURITY CHECK
        # ====================================================

        validate_file_size(
            final_file
        )

        # ====================================================
        # USER DOWNLOAD NAME
        # ====================================================

        short_id = job_id[:6].upper()

        download_name = (
            f"PullClips - Clip "
            f"{short_id}"
            f"{final_file.suffix}"
        )

        print(
            f"✅ Download complete "
            f"[{job_id}] "
            f"{final_file.name}"
        )

        # ====================================================
        # RESPONSE
        # ====================================================

        return {
            "success": True,
            "filename": final_file.name,
            "download_name": download_name,
            "title": title,
            "download_url": (
                f"/download/"
                f"{final_file.name}"
            ),
        }

    except HTTPException:

        delete_job_files(
            job_id
        )

        raise

    except Exception as e:

        error_text = str(e)

        print(
            f"❌ Download failed "
            f"[{job_id}]: "
            f"{error_text}"
        )

        delete_job_files(
            job_id
        )

        if (
            "429" in error_text
            or "Too Many Requests" in error_text
        ):

            raise HTTPException(
                status_code=503,
                detail=(
                    "The source website temporarily "
                    "rate-limited this server. "
                    "Please try again later."
                ),
            )

        if (
            "Sign in to confirm"
            in error_text
            or "not a bot"
            in error_text
        ):

            raise HTTPException(
                status_code=503,
                detail=(
                    "The source website requires "
                    "additional verification."
                ),
            )

        raise HTTPException(
            status_code=502,
            detail=(
                "Could not download this video. "
                "The link may be unavailable, "
                "private, or unsupported."
            ),
        )

    finally:

        download_slots.release()


# ============================================================
# DOWNLOAD FILE
# ============================================================

@app.get(
    "/download/{filename}"
)
def download_file(
    filename: str,
):

    # ========================================================
    # BLOCK PATH TRAVERSAL
    # ========================================================

    if (
        "/" in filename
        or "\\" in filename
        or ".." in filename
    ):

        raise HTTPException(
            status_code=400,
            detail="Invalid filename.",
        )

    # Only allow UUID-style generated files.
    if "." not in filename:

        raise HTTPException(
            status_code=400,
            detail="Invalid filename.",
        )

    file_path = DOWNLOADS / filename

    # ========================================================
    # SAFE PATH RESOLUTION
    # ========================================================

    try:

        downloads_root = DOWNLOADS.resolve()
        resolved_file = file_path.resolve()

        if (
            resolved_file.parent
            != downloads_root
        ):

            raise HTTPException(
                status_code=400,
                detail="Invalid filename.",
            )

    except HTTPException:

        raise

    except Exception:

        raise HTTPException(
            status_code=400,
            detail="Invalid filename.",
        )

    # ========================================================
    # EXISTS?
    # ========================================================

    if not file_path.exists():

        raise HTTPException(
            status_code=404,
            detail=(
                "This download has expired "
                "or no longer exists."
            ),
        )

    if not file_path.is_file():

        raise HTTPException(
            status_code=404,
            detail="File not found.",
        )

    # ========================================================
    # SIZE CHECK
    # ========================================================

    validate_file_size(
        file_path
    )

    # ========================================================
    # USER FILENAME
    # ========================================================

    short_id = (
        file_path.stem[:6].upper()
    )

    download_name = (
        f"PullClips - Clip "
        f"{short_id}"
        f"{file_path.suffix}"
    )

    # ========================================================
    # DELETE AFTER DOWNLOAD
    # ========================================================

    def delete_after_download():

        try:

            file_path.unlink(
                missing_ok=True
            )

            print(
                f"🗑️ Deleted after download: "
                f"{filename}"
            )

        except Exception as e:

            print(
                f"⚠️ Could not delete "
                f"{filename}: {e}"
            )

    return FileResponse(
        path=str(file_path),
        filename=download_name,
        media_type="application/octet-stream",
        background=BackgroundTask(
            delete_after_download
        ),
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup():

    print("")
    print("🚀 PullClip API started")

    print(
        f"📁 Downloads: "
        f"{DOWNLOADS.resolve()}"
    )

    print(
        f"🎥 Max resolution: "
        f"{MAX_VIDEO_HEIGHT}p"
    )

    print(
        f"⏱️ Max duration: "
        f"{MAX_DURATION_SECONDS // 60} minutes"
    )

    print(
        f"📦 Max file size: "
        f"{MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB"
    )

    print(
        f"⚡ Max concurrent downloads: "
        f"{MAX_CONCURRENT_DOWNLOADS}"
    )

    print(
        f"🦕 Deno detected: "
        f"{DENO_PATH}"
    )

    print("")

