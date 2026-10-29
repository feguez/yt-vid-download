# YouTube MP4 Downloader

A small local Python utility: paste one YouTube video URL, inspect its metadata, and save an MP4 with audio. No API keys, accounts, database, or hosted server.

Uses Python 3.11+, yt-dlp, FastAPI, and a plain HTML/CSS/JavaScript interface. Choose a quality ceiling of 360p, 480p, 720p, or 1080p (default: 480p). The highest available resolution under that ceiling is selected, with combined audio/video preferred at equal resolution. Separate streams require FFmpeg; the app explains how to install it instead of silently choosing a lower-resolution combined file. Recorded watch, youtu.be, Shorts, embed, and live-page links are supported; playlists and active livestreams are excluded.

## Install (Windows PowerShell)

```powershell
git clone https://github.com/feguez/yt-vid-download.git
cd yt-vid-download
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Using the virtual environment's Python directly avoids PowerShell activation policy changes.

## Step 1: use the downloader

```powershell
.\.venv\Scripts\python.exe downloader.py "https://www.youtube.com/watch?v=VIDEO_ID" --quality 480
```

For Full HD, use `--quality 1080`, or select **1080p MP4 (up to)** in the browser. The source must offer that resolution; lower-resolution sources are not upscaled.

Replace `VIDEO_ID` with the video's actual 11-character ID. The CLI prints the saved path under `downloads/video-*/video.mp4` and keeps that file. Each download has its own folder. Errors describe invalid URLs, unavailable videos, missing formats, or missing FFmpeg.

## Steps 2–3: run the API and webpage

```powershell
.\.venv\Scripts\python.exe app.py
```

Open <http://127.0.0.1:8000>. Get video info, choose a quality ceiling, and click **Download MP4**. Stop with Ctrl+C. The app binds to `127.0.0.1`; do not expose it on your network. Interactive API documentation is at <http://127.0.0.1:8000/docs>.

Chromium browsers supporting the File System Access API show a save dialog and stream the response to disk. Other browsers buffer the response as a Blob before starting a browser download. Server responses always stream from disk without loading the video into server memory. API staging files are removed after delivery; a forcibly terminated process can leave a `downloads/video-*` folder, which you may delete after stopping the app. One extraction/download runs at a time; another download request receives HTTP 409 while it is busy.

### API examples

```powershell
$url = "https://www.youtube.com/watch?v=VIDEO_ID"
Invoke-RestMethod ("http://127.0.0.1:8000/api/info?url=" + [uri]::EscapeDataString($url))
$body = @{ url = $url; quality = 480 } | ConvertTo-Json
Invoke-WebRequest "http://127.0.0.1:8000/api/download" -Method Post -ContentType "application/json" -Body $body -OutFile "video.mp4"
```

`GET /api/info` returns JSON like:

```json
{"id":"BaW_jenozKc","title":"Example video","duration":1200,"thumbnail":"https://i.ytimg.com/vi/BaW_jenozKc/hqdefault.jpg"}
```

`POST /api/download` accepts `{"url":"...","quality":480}` and returns an attachment with `Content-Type: video/mp4`. Duration is in seconds and may be null. Errors use `{"detail":"Explanation"}`: 400 invalid URL/quality or unsupported live video, 404 unavailable video, 409 missing FFmpeg or busy downloader, 422 invalid request body or no suitable MP4 format, 502 YouTube extraction/blocking failure, 500 local filesystem failure.

### Audio language

Click **Get video info**, then choose **Audio language** before downloading. The default **Original audio** uses yt-dlp's original-track label rather than picking the highest-bitrate dub. If only one labeled language exists, that track is used; if multiple languages exist without an identifiable original, the app asks you to choose explicitly. If the source provides no language labels, its unlabeled audio is used without claiming to identify its spoken language.

The API accepts an optional `language` field: `"original"` (default), or an available code from the metadata response's `audio_languages`, such as `"en"` or `"de"`. The CLI supports `--language en`. Unavailable language choices return HTTP 422 rather than downloading another language. Audio language applies to combined MP4 files as well as separate audio streams; choosing a particular track may require FFmpeg even at lower quality.

### FFmpeg and extraction requirements

Combined MP4 audio/video formats work without FFmpeg. Higher resolutions, especially 720p and 1080p, usually use separate MP4 video and M4A audio. To download these, install FFmpeg, then open a new terminal and restart the app:

```powershell
winget install --id Gyan.FFmpeg -e
ffmpeg -version
```

yt-dlp merges these streams into an MP4; the app never renames a WebM as MP4. It checks the output container header, while format selection requires both video and audio. See [yt-dlp format documentation](https://github.com/yt-dlp/yt-dlp#format-selection).

YouTube extraction changes frequently. Update first if extraction fails:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade "yt-dlp[default]"
```

Some videos require a JavaScript runtime; the app enables Node.js when it is on PATH. See [yt-dlp's JavaScript runtime guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS). TLS verification uses the operating system's trusted certificates. YouTube can still block extraction or require sign-in. The app reports that failure and does not implement CAPTCHA bypasses, cookies, authentication workarounds, or restrictions bypasses.

## Step 4: verify

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Tests mock YouTube to cover URL validation, metadata, format selection, 20-minute metadata, file responses, partial-file cleanup, wrong-container rejection, and API errors. They do not download large videos or prove live YouTube availability. For a manual check, use a short video you own or are authorized to download, save it, and play it to confirm both audio and video. With FFmpeg installed, inspect with `ffprobe -v error -show_streams video.mp4`. A standard-length manual download is useful for checking your connection and disk usage; file streaming makes server response memory usage independent of video size.

Initial verification: all 20 mocked tests passed on Windows with Python 3.12. Live metadata and a single download of Blender's Big Buck Bunny (`aqz-KE-bpKQ`) succeeded at 360p, producing a 27.2 MiB MP4 lasting about 635 seconds. PyAV, installed only for local verification, decoded both 640×360 H.264 video and 44.1 kHz AAC audio. A full 20-minute download and browser save-dialog interaction have not been manually tested.

Download only when authorized by the service, rights holder, or applicable law.
