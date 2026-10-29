from unittest.mock import patch

import pytest

import app
import downloader as d
from fastapi.testclient import TestClient


def multilingual():
    return [
        dict(format_id="video", ext="mp4", vcodec="avc1", acodec="none", height=1080),
        dict(format_id="german-combined", ext="mp4", vcodec="avc1", acodec="aac", height=360, language="de"),
        dict(format_id="english", ext="m4a", vcodec="none", acodec="aac", language="en", abr=128, language_preference=10, format_note="English (original)"),
        dict(format_id="german", ext="m4a", vcodec="none", acodec="aac", language="de", abr=256),
    ]


def test_original_over_higher_bitrate_dub():
    assert d.select_format(multilingual(), 1080, True) == "video+english"


def test_explicit_dub():
    assert d.select_format(multilingual(), 1080, True, "de") == "video+german"
    assert d.select_format(multilingual(), 360, False, "de") == "german-combined"


def test_combined_wrong_language_is_not_selected():
    available = multilingual()
    available[0]["height"] = 360
    assert d.select_format(available, 360, True, "en") == "video+english"
    with pytest.raises(d.DownloaderError, match="FFmpeg"):
        d.select_format(available, 360, False, "en")


def test_unknown_original_requires_choice():
    available = multilingual()
    available[2].pop("language_preference")
    available[2].pop("format_note")
    with pytest.raises(d.DownloaderError, match="choose an audio language"):
        d.select_format(available, 1080, True)
    assert d.select_format(available, 1080, True, "en") == "video+english"


def test_missing_language_never_falls_back():
    with pytest.raises(d.DownloaderError, match="unavailable") as error:
        d.select_format(multilingual(), 1080, True, "es")
    assert error.value.status_code == 422


def test_single_language_and_unlabeled_sources():
    available = multilingual()[:1] + [multilingual()[-1]]
    assert d.select_format(available, 1080, True) == "video+german"
    available[-1].pop("language")
    assert d.select_format(available, 1080, True) == "video+german"
    with pytest.raises(d.DownloaderError, match="unavailable"):
        d.select_format(available, 1080, True, "en")


def test_metadata_language_choices():
    with patch.object(d, "_metadata", return_value={"id": "BaW_jenozKc", "formats": multilingual()}):
        info = d.get_video_info("https://youtu.be/BaW_jenozKc")
    assert info["audio_languages"] == ["de", "en"]
    assert info["original_audio_language"] == "en"


def test_api_passes_selected_language(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"test")
    result = d.Download(path, tmp_path / "unused", "video.mp4")
    with patch.object(app, "download_video", return_value=result) as download:
        response = TestClient(app.app).post("/api/download", json={"url": "https://youtu.be/BaW_jenozKc", "quality": 1080, "language": "en"})
    assert response.status_code == 200
    download.assert_called_once_with("https://youtu.be/BaW_jenozKc", 1080, language="en")
