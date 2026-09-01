
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
    version="1.3.0",
)


# ============================================================
# CORS
# ============================================================

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
    shutil.which("deno"),
    "/opt/render/.deno/bin/deno",
    "/opt/render/project/src/.deno/bin/deno",
    str(Path.cwd() / ".deno" / "bin" / "deno"),
]

DENO_PATH = None

for candidate in DENO_CANDIDATES:
    if not candidate:
        continue

    try:
        candidate_path = Path(candidate)

        if (
            candidate_path.is_file()
            and os.access(candidate_path, os.X_OK)
        ):
            DENO_PATH = str(candidate_path)
            break

    except Exception:
        continue


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
            check=False,
        )

        print("")
        print("Deno version:")

        if result.stdout:
            print(result.stdout.strip())

        if result.stderr:
            print("")
            print("Deno stderr:")
            print(result.stderr.strip())

    except Exception as e:
        print("")
        print("Deno check failed:")
        print(e)

else:
    print("")
    print("DENO NOT FOUND")
    print("Checked these locations:")

    for candidate in DENO_CANDIDATES:
        if candidate:
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
        "version": "1.3.0",
        "deno_available": DENO_PATH is not None,
        "ytdlp_version": yt_dlp.version.__version__,
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
        "version": "1.3.0",
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
# IDENTIFY DOMAIN
# ============================================================

def get_hostname(url: str) -> str:
    try:
        parsed = urlparse(url)

        hostname = parsed.hostname or ""

        hostname = hostname.lower().rstrip(".")

        if hostname.startswith("www."):
            hostname = hostname[4:]

        return hostname

    except Exception:
        return ""


def is_tiktok_url(url: str) -> bool:
    hostname = get_hostname(url)

    return (
        hostname == "tiktok.com"
        or hostname.endswith(".tiktok.com")
    )


# ============================================================
# RATE LIMIT
# ============================================================

def check_rate_limit(request: Request):
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
                    "Please wait a moment and try again."
                ),
            )

        request_log[ip].append(now)


# ============================================================
# URL INPUT VALIDATION
# ============================================================

def validate_url_or_raise(url: str):
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

def delete_job_files(job_id: str):
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
                    f"Deleted: {file_path.name}"
                )

        except Exception as e:
            print(
                f"Could not delete "
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
                            "Removed old file: "
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
# YT-DLP RUNTIME OPTIONS
# ============================================================

def get_ytdlp_runtime_options():
    """
    Build shared yt-dlp runtime configuration.

    Deno is supplied when available because modern
    yt-dlp extractors can require a JavaScript runtime.
    """

    options = {}

    if DENO_PATH:
        options["js_runtimes"] = {
            "deno": {
                "path": DENO_PATH,
            }
        }

    # Generic browser headers.
    #
    # These do NOT bypass authentication, CAPTCHA,
    # or access restrictions.
    options["http_headers"] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    return options


# ============================================================
# YT-DLP INFO OPTIONS
# ============================================================

def build_info_options():
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,

        "socket_timeout": YTDLP_TIMEOUT,

        "retries": 2,
        "fragment_retries": 2,
    }

    options.update(
        get_ytdlp_runtime_options()
    )

    return options


# ============================================================
# YT-DLP DOWNLOAD OPTIONS
# ============================================================

def build_download_options(
    format_selector: str,
    output_template: str,
):
    options = {
        "format": format_selector,

        "outtmpl": output_template,

        "noplaylist": True,

        "quiet": True,

        "no_warnings": True,

        "socket_timeout": YTDLP_TIMEOUT,

        "retries": 2,

        "fragment_retries": 2,

        "merge_output_format": "mp4",

        "continuedl": False,

        "overwrites": False,
    }

    options.update(
        get_ytdlp_runtime_options()
    )

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
            f"File too large: {size} bytes"
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
# ERROR CLASSIFICATION
# ============================================================

def classify_ytdlp_error(
    error_text: str,
    url: str,
) -> HTTPException:

    text = error_text.lower()

    # Rate limiting
    if (
        "429" in text
        or "too many requests" in text
        or "rate limit" in text
    ):
        return HTTPException(
            status_code=503,
            detail=(
                "The source website temporarily "
                "rate-limited this server. "
                "Please try again later."
            ),
        )

    # Login / verification / anti-bot
    verification_patterns = [
        "sign in to confirm",
        "not a bot",
        "verify you are human",
        "captcha",
        "verification required",
        "login required",
        "log in to continue",
        "access denied",
        "blocked",
    ]

    if any(
        pattern in text
        for pattern in verification_patterns
    ):
        if is_tiktok_url(url):
            return HTTPException(
                status_code=503,
                detail=(
                    "TikTok is currently requiring "
                    "verification or restricting automated "
                    "access for this request. "
                    "Please try again later or use a "
                    "publicly accessible TikTok video."
                ),
            )

        return HTTPException(
            status_code=503,
            detail=(
                "The source website requires "
                "additional verification or login."
            ),
        )

    # Private / unavailable
    unavailable_patterns = [
        "video unavailable",
        "private video",
        "video is private",
        "not available",
        "does not exist",
        "could not find",
    ]

    if any(
        pattern in text
        for pattern in unavailable_patterns
    ):
        return HTTPException(
            status_code=404,
            detail=(
                "This video is unavailable, private, "
                "or no longer exists."
            ),
        )

    # Generic extractor failure
    if is_tiktok_url(url):
        return HTTPException(
            status_code=502,
            detail=(
                "TikTok could not provide this video "
                "to the downloader. TikTok may have "
                "changed its access requirements or "
                "the video may not be publicly accessible."
            ),
        )

    return HTTPException(
        status_code=502,
        detail=(
            "Could not read this video. "
            "The link may be private, invalid, "
            "temporarily unavailable, or blocked "
            "by the source website."
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

    ydl_opts = build_info_options()

    try:
        print(
            f"Checking URL: {url}"
        )

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
            "Check failed:"
        )

        print(error_text)

        raise classify_ytdlp_error(
            error_text,
            url,
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

        # ====================================================
        # VIDEO
        # ====================================================

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
                        f"{str(ext or '').upper()}"
                    ),
                }
            )

        # ====================================================
        # AUDIO
        # ====================================================

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
                        f"{str(ext or '').upper()}"
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

        info_opts = build_info_options()

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

        ydl_opts = build_download_options(
            format_selector=format_selector,
            output_template=output_template,
        )

        # ====================================================
        # DOWNLOAD
        # ====================================================

        print(
            f"Starting download [{job_id}]"
        )

        print(
            f"URL: {url}"
        )

        print(
            f"Format: {format_selector}"
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
            f"Download complete [{job_id}] "
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
            f"Download failed [{job_id}]:"
        )

        print(
            error_text
        )

        delete_job_files(
            job_id
        )

        raise classify_ytdlp_error(
            error_text,
            url,
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

    # Only generated UUID-style filenames
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

        downloads_root = (
            DOWNLOADS.resolve()
        )

        resolved_file = (
            file_path.resolve()
        )

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
                f"Deleted after download: "
                f"{filename}"
            )

        except Exception as e:

            print(
                f"Could not delete "
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

    print("PullClip API started")

    print(
        f"Downloads: "
        f"{DOWNLOADS.resolve()}"
    )

    print(
        f"Max resolution: "
        f"{MAX_VIDEO_HEIGHT}p"
    )

    print(
        f"Max duration: "
        f"{MAX_DURATION_SECONDS // 60} minutes"
    )

    print(
        f"Max file size: "
        f"{MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB"
    )

    print(
        f"Max concurrent downloads: "
        f"{MAX_CONCURRENT_DOWNLOADS}"
    )

    print(
        f"Deno detected: "
        f"{DENO_PATH}"
    )

    print(
        f"yt-dlp version: "
        f"{yt_dlp.version.__version__}"
    )

    print("")

