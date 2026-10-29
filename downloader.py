"""Small synchronous downloader, shared by the CLI and local API."""

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import parse_qs, urlsplit

import yt_dlp
from yt_dlp.utils import DownloadError

DOWNLOADS = Path(__file__).resolve().parent / "downloads"
QUALITIES = (360, 480, 720, 1080)


class DownloaderError(Exception):
    """An error safe to explain to the user."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def validate_url(url: str) -> str:
    """Accept ordinary video URLs only; always return a canonical watch URL."""
    try:
        parsed = urlsplit(url.strip())
        if (parsed.scheme not in {"http", "https"} or parsed.username
                or parsed.password or parsed.port not in {None, 80, 443}):
            raise ValueError
        host = (parsed.hostname or "").lower()
        if host == "youtu.be":
            video_id = parsed.path.removeprefix("/")
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            if parsed.path == "/watch":
                ids = parse_qs(parsed.query).get("v", [])
                video_id = ids[0] if len(ids) == 1 else ""
            else:
                match = re.fullmatch(r"/(?:shorts|embed|live)/([\w-]{11})", parsed.path)
                video_id = match.group(1) if match else ""
        else:
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError
    except (ValueError, AttributeError):
        raise DownloaderError("Enter a valid YouTube video URL (watch, youtu.be, or Shorts).", 400) from None
    return f"https://www.youtube.com/watch?v={video_id}"


def _options() -> dict:
    return {"quiet": True, "no_warnings": True, "noplaylist": True,
            "socket_timeout": 30, "retries": 2, "fragment_retries": 2,
            "cachedir": False, "compat_opts": {"no-certifi"},
            "js_runtimes": {"node": {}} if shutil.which("node") else {}}


def _extract_error(exc: DownloadError) -> DownloaderError:
    message = str(exc).lower()
    if "certificate_verify_failed" in message:
        return DownloaderError("TLS certificate verification failed. Check your Windows trusted certificates or network administrator's certificate setup.")
    if any(word in message for word in ("private video", "removed", "unavailable", "not available")):
        return DownloaderError("This video is unavailable, private, or restricted in your region.", 404)
    if any(word in message for word in ("sign in", "bot", "captcha", "403", "429")):
        return DownloaderError("YouTube blocked this request or requires sign-in. No bypass is attempted. Update yt-dlp and try later.")
    return DownloaderError("YouTube could not provide the video. Check your connection, update yt-dlp, and try again.")


def _metadata(url: str) -> dict:
    try:
        with yt_dlp.YoutubeDL(_options()) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        raise _extract_error(exc) from exc
    if not info or info.get("_type") in {"playlist", "multi_video"}:
        raise DownloaderError("The URL must identify one video.", 400)
    if info.get("is_live") or info.get("live_status") == "is_upcoming":
        raise DownloaderError("Live and upcoming streams are not supported. Choose a recorded video.", 400)
    return info


def get_video_info(url: str) -> dict:
    info = _metadata(validate_url(url))
    return {"id": info["id"], "title": info.get("title", "YouTube video"),
            "duration": info.get("duration"), "thumbnail": info.get("thumbnail")}


def select_format(formats: list[dict], quality: int, ffmpeg: bool) -> str:
    """Choose the highest resolution under the cap; prefer combined at equal height."""
    def video_ok(f):
        return (f.get("ext") == "mp4" and f.get("vcodec") not in {None, "none"}
                and 0 < (f.get("height") or 0) <= quality and not f.get("has_drm"))

    def rank(f):
        return (f.get("height") or 0, f.get("tbr") or 0)

    combined = [f for f in formats if video_ok(f) and f.get("acodec") not in {None, "none"}]
    videos = [f for f in formats if video_ok(f) and f.get("acodec") == "none"]
    audio = [f for f in formats if f.get("ext") == "m4a" and f.get("acodec") not in {None, "none"}
             and f.get("vcodec") == "none" and not f.get("has_drm")]
    best_combined = max(combined, key=rank) if combined else None
    best_video = max(videos, key=rank) if videos and audio else None
    if best_combined and (not best_video or best_combined["height"] >= best_video["height"]):
        return best_combined["format_id"]
    if best_video:
        if not ffmpeg:
            raise DownloaderError("This video needs FFmpeg to combine audio and video. Install it with: winget install --id Gyan.FFmpeg -e", 409)
        return best_video["format_id"] + "+" + max(audio, key=lambda f: f.get("abr") or 0)["format_id"]
    raise DownloaderError(f"No MP4 with audio is available at {quality}p or below.", 422)


@dataclass
class Download:
    path: Path
    directory: Path
    filename: str

    def cleanup(self):
        shutil.rmtree(self.directory, ignore_errors=True)


def download_video(url: str, quality: int = 480, output_dir: Path = DOWNLOADS) -> Download:
    url = validate_url(url)
    if quality not in QUALITIES:
        raise DownloaderError("Choose 360p, 480p, 720p, or 1080p.", 400)
    info = _metadata(url)
    selected = select_format(info.get("formats", []), quality, shutil.which("ffmpeg") is not None)
    output_dir.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="video-", dir=output_dir))
    try:
        options = {**_options(), "format": selected, "outtmpl": str(directory / "video.%(ext)s"),
                   "merge_output_format": "mp4"}
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(url, download=True)
        path = directory / "video.mp4"
        # ISO base media files have an ftyp box: do not just rename arbitrary data.
        if not path.is_file():
            raise DownloaderError("The downloader did not produce an MP4 file.")
        with path.open("rb") as file:
            header = file.read(12)
        if len(header) < 12 or header[4:8] != b"ftyp":
            raise DownloaderError("The downloaded file is not an MP4 container.")
        title = re.sub(r'[^\w .()-]', "_", info.get("title") or "video")[:100].strip(" .") or "video"
        return Download(path, directory, f"{title} [{info['id']}].mp4")
    except BaseException as exc:
        shutil.rmtree(directory, ignore_errors=True)
        if isinstance(exc, DownloadError):
            raise _extract_error(exc) from exc
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download one authorized YouTube video as MP4.")
    parser.add_argument("url")
    parser.add_argument("--quality", type=int, choices=QUALITIES, default=480)
    args = parser.parse_args()
    try:
        result = download_video(args.url, args.quality)
        print(f"Saved: {result.path}")
    except (DownloaderError, OSError) as exc:
        parser.exit(1, f"Download failed: {exc}\n")
