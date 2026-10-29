from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from yt_dlp.utils import DownloadError

import app
import downloader as d

URL = "https://www.youtube.com/watch?v=BaW_jenozKc"
client = TestClient(app.app)


@pytest.mark.parametrize("url", [URL, "https://youtu.be/BaW_jenozKc?t=1", "https://m.youtube.com/shorts/BaW_jenozKc", URL + "&list=ignored"])
def test_valid_urls(url):
    assert d.validate_url(url) == URL


@pytest.mark.parametrize("url", ["", "file:///etc/passwd", "https://youtube.com.evil.test/watch?v=BaW_jenozKc", "https://youtube.com/playlist?list=123", "https://youtube.com/watch?v=bad", "https://youtube.com:invalid/watch?v=BaW_jenozKc", "https://evil.test", "https://user@youtube.com/watch?v=BaW_jenozKc"])
def test_invalid_urls(url):
    with pytest.raises(d.DownloaderError) as error:
        d.validate_url(url)
    assert error.value.status_code == 400


def formats():
    return [dict(format_id="18", ext="mp4", vcodec="avc1", acodec="mp4a", height=360),
            dict(format_id="135", ext="mp4", vcodec="avc1", acodec="none", height=480),
            dict(format_id="140", ext="m4a", vcodec="none", acodec="mp4a", abr=128)]


def test_format_selection():
    assert d.select_format(formats(), 360, False) == "18"
    assert d.select_format(formats(), 480, True) == "135+140"
    assert d.select_format(formats()[1:], 480, True) == "135+140"
    with pytest.raises(d.DownloaderError, match="FFmpeg"):
        d.select_format(formats()[1:], 480, False)
    with pytest.raises(d.DownloaderError, match="No MP4"):
        d.select_format(formats()[1:], 360, True)


def fake_extract(url, download=False):
    return {"id": "BaW_jenozKc", "title": "Test video", "duration": 1200,
            "thumbnail": "https://i.ytimg.com/test.jpg", "formats": formats()}


def test_metadata():
    with patch.object(d.yt_dlp.YoutubeDL, "extract_info", side_effect=fake_extract):
        response = client.get("/api/info", params={"url": URL})
    assert response.status_code == 200
    assert response.json()["duration"] == 1200
    assert response.json()["id"] == "BaW_jenozKc"


def test_errors():
    assert client.get("/api/info", params={"url": "https://example.org"}).status_code == 400
    with patch.object(d.yt_dlp.YoutubeDL, "extract_info", side_effect=DownloadError("Video unavailable")):
        response = client.get("/api/info", params={"url": URL})
    assert response.status_code == 404
    assert "unavailable" in response.json()["detail"]
    with patch.object(d.yt_dlp.YoutubeDL, "extract_info", side_effect=DownloadError("Sign in to confirm you're not a bot")):
        response = client.get("/api/info", params={"url": URL})
    assert response.status_code == 502
    assert "No bypass" in response.json()["detail"]


def test_download_and_cleanup(tmp_path):
    def extract(instance, url, download=False):
        if download:
            Path(instance.params["outtmpl"]["default"].replace("%(ext)s", "mp4")).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"test")
        return fake_extract(url)
    with patch.object(d.yt_dlp.YoutubeDL, "extract_info", extract):
        result = d.download_video(URL, quality=360, output_dir=tmp_path)
    assert result.path.suffix == ".mp4"
    assert result.path.exists()
    with patch.object(app, "download_video", return_value=result):
        response = client.post("/api/download", json={"url": URL})
    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert "attachment" in response.headers["content-disposition"]
    assert response.content[4:8] == b"ftyp"
    assert not result.directory.exists()


def test_partial_download_cleanup(tmp_path):
    def extract(instance, url, download=False):
        if download:
            Path(instance.params["outtmpl"]["default"].replace("%(ext)s", "mp4.part")).write_bytes(b"partial")
            raise DownloadError("connection failed")
        return fake_extract(url)
    with patch.object(d.yt_dlp.YoutubeDL, "extract_info", extract):
        with pytest.raises(d.DownloaderError):
            d.download_video(URL, quality=360, output_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_wrong_container_rejected(tmp_path):
    def extract(instance, url, download=False):
        if download:
            Path(instance.params["outtmpl"]["default"].replace("%(ext)s", "mp4")).write_bytes(b"not an mp4 file")
        return fake_extract(url)
    with patch.object(d.yt_dlp.YoutubeDL, "extract_info", extract):
        with pytest.raises(d.DownloaderError, match="container"):
            d.download_video(URL, quality=360, output_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_api_validation_and_local_access():
    assert client.get("/").status_code == 200
    assert client.post("/api/download", json={}).status_code == 422
    assert client.post("/api/download", json={"url": URL, "quality": 2160}).status_code == 400
    assert client.get("/", headers={"Host": "evil.test"}).status_code == 403
    assert client.post("/api/download", json={"url": URL}, headers={"Origin": "https://evil.test"}).status_code == 403


def test_busy_download():
    with app.download_lock:
        assert client.post("/api/download", json={"url": URL}).status_code == 409


@pytest.mark.parametrize("quality,expected", [(360, "18"), (480, "135+140"), (720, "136+140"), (1080, "137+140")])
def test_resolution_caps(quality, expected):
    available = formats() + [
        dict(format_id=identifier, ext="mp4", vcodec="avc1", acodec="none", height=height)
        for identifier, height in [("136", 720), ("137", 1080), ("2160", 2160)]
    ]
    assert d.select_format(available, quality, True) == expected


def test_high_quality_requires_ffmpeg_instead_of_silent_downgrade():
    with pytest.raises(d.DownloaderError, match="FFmpeg") as error:
        d.select_format(formats(), 1080, False)
    assert error.value.status_code == 409


def test_combined_preferred_at_same_resolution():
    available = formats() + [dict(format_id="combined480", ext="mp4", vcodec="avc1", acodec="mp4a", height=480)]
    assert d.select_format(available, 1080, False) == "combined480"


@pytest.mark.parametrize("quality", [720, 1080])
def test_api_accepts_hd_quality(quality, tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"test")
    result = d.Download(path, tmp_path / "unused", "video.mp4")
    with patch.object(app, "download_video", return_value=result) as download:
        response = client.post("/api/download", json={"url": URL, "quality": quality})
    assert response.status_code == 200
    download.assert_called_once_with(URL, quality)

