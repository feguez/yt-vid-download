"""Run with python app.py; keep this personal utility bound to localhost."""

import logging
from pathlib import Path
import threading

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from downloader import DownloaderError, download_video, get_video_info

app = FastAPI(title="YouTube MP4 Downloader")
download_lock = threading.Lock()


@app.middleware("http")
async def local_only(request: Request, call_next):
    # Reject DNS rebinding and cross-origin browser requests to this local API.
    if request.url.hostname not in {"localhost", "127.0.0.1", "::1", "testserver"}:
        return JSONResponse({"detail": "Use localhost to access this application."}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
        return JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=403)
    return await call_next(request)


@app.exception_handler(DownloaderError)
async def download_error(request: Request, exc: DownloaderError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)


@app.exception_handler(OSError)
async def filesystem_error(request: Request, exc: OSError):
    logging.exception("Filesystem or process error", exc_info=exc)
    return JSONResponse({"detail": "Could not save the video. Check free disk space and folder permissions."}, status_code=500)


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/info")
def video_info(url: str):
    return get_video_info(url)


class DownloadRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    quality: int = 480
    language: str = Field(default="original", min_length=1, max_length=40)


@app.post("/api/download")
def video_download(body: DownloadRequest):
    if not download_lock.acquire(blocking=False):
        raise DownloaderError("Another download is running. Try again when it finishes.", 409)
    try:
        result = download_video(body.url, body.quality, language=body.language)
    finally:
        download_lock.release()
    return FileResponse(result.path, media_type="video/mp4", filename=result.filename,
                        background=BackgroundTask(result.cleanup))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
