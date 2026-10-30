"""Tests for youtube_ops.upload — the request it builds, its guards, and the CLI wiring."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cli"))

import youtube_ops
from output import ValidationError


@pytest.fixture
def video(tmp_path):
    clip = tmp_path / "short.mp4"
    clip.write_bytes(b"\x00\x00")
    return clip


def _service(status):
    service = MagicMock()
    service.videos.return_value.insert.return_value.execute.return_value = {"id": "vid123", "status": status}
    return service


def _upload(path, **kwargs):
    args = {"title": "A title", "description": "A description", "privacy": "public", "made_for_kids": False}
    args.update(kwargs)
    return youtube_ops.upload(str(path), **args)


class TestUpload:
    def test_request_carries_metadata_status_and_resumable_media(self, video):
        service = _service({"privacyStatus": "public", "uploadStatus": "uploaded"})
        with patch("youtube_ops.build_service", return_value=service), \
             patch("youtube_ops.MediaFileUpload") as media:
            out = _upload(video, tags=["lab", "ai"], category_id="28")

        _, kwargs = service.videos.return_value.insert.call_args
        assert kwargs["part"] == "snippet,status"
        assert kwargs["body"] == {
            "snippet": {"title": "A title", "description": "A description", "tags": ["lab", "ai"], "categoryId": "28"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
        }
        assert media.call_args.kwargs == {"mimetype": "video/mp4", "resumable": True}
        assert out["id"] == "vid123" and out["url"] == "https://www.youtube.com/watch?v=vid123"
        assert out["privacy_requested"] == "public" and out["privacy"] == "public"
        assert "check_after_processing" in out

    def test_private_upload_needs_no_after_check(self, video):
        with patch("youtube_ops.build_service", return_value=_service({"privacyStatus": "private"})), \
             patch("youtube_ops.MediaFileUpload"):
            out = _upload(video, privacy="private")
        assert "check_after_processing" not in out

    def test_scheduled_publish_is_private_with_publish_at(self, video):
        service = _service({"privacyStatus": "private", "publishAt": "2026-11-03T15:00:00Z"})
        with patch("youtube_ops.build_service", return_value=service), patch("youtube_ops.MediaFileUpload"):
            out = _upload(video, privacy="private", publish_at="2026-11-03T15:00:00Z")
        _, kwargs = service.videos.return_value.insert.call_args
        assert kwargs["body"]["status"]["publishAt"] == "2026-11-03T15:00:00Z"
        assert out["publish_at"] == "2026-11-03T15:00:00Z" and "check_after_processing" in out

    @pytest.mark.parametrize("kwargs, message", [
        ({"privacy": "public", "publish_at": "2026-11-03T15:00:00Z"}, "needs --privacy private"),
        ({"made_for_kids": None}, "made for kids"),
        ({"title": "x" * 101}, "1 to 100 characters"),
        ({"title": "  "}, "1 to 100 characters"),
        ({"description": "é" * 2501}, "5000 bytes"),
        ({"privacy": "friends"}, "must be one of"),
    ])
    def test_guards_refuse_before_any_api_call(self, video, kwargs, message):
        with patch("youtube_ops.build_service") as build, pytest.raises(ValidationError, match=message):
            _upload(video, **kwargs)
        build.assert_not_called()

    def test_missing_file(self):
        with pytest.raises(ValidationError, match="File not found"):
            _upload("/nonexistent/short.mp4")


CLI = str(Path(__file__).resolve().parent.parent / "cli" / "gw.py")


def _run(*args):
    """Run `gw youtube upload` in a subprocess, as test_cli does; the guards stop it before any API call."""
    result = subprocess.run([sys.executable, CLI, "youtube", "upload", *args],
                            capture_output=True, text=True, timeout=10)
    return result.stdout + result.stderr, result.returncode


class TestCli:
    def test_privacy_has_no_default(self, video):
        output, code = _run(str(video), "--title", "T", "--description", "D", "--not-made-for-kids")
        assert code != 0 and "--privacy" in output

    def test_refuses_both_description_sources(self, video, tmp_path):
        desc = tmp_path / "d.txt"
        desc.write_text("D", encoding="utf-8")
        output, code = _run(str(video), "--title", "T", "--description", "D", "--description-file", str(desc),
                            "--privacy", "private", "--not-made-for-kids")
        assert code != 0 and "exactly one" in output

    def test_description_file_and_tags_reach_the_upload(self, video, tmp_path):
        from gw import cli
        desc = tmp_path / "d.txt"
        desc.write_text("From a file\nwith links", encoding="utf-8")
        with patch("youtube_ops.upload", return_value={"uploaded": True}) as up, \
             patch("commands.youtube.output_success"):
            result = CliRunner().invoke(cli, ["youtube", "upload", str(video), "--title", "T",
                                              "--description-file", str(desc), "--privacy", "private",
                                              "--not-made-for-kids", "--tags", "a, b"])
        assert result.exit_code == 0, result.output
        assert up.call_args.kwargs["description"] == "From a file\nwith links"
        assert up.call_args.kwargs["tags"] == ["a", "b"]
        assert up.call_args.kwargs["made_for_kids"] is False
