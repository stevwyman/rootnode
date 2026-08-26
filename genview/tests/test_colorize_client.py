import json
from unittest.mock import MagicMock, mock_open, patch

from django.test import SimpleTestCase
import requests

from genview.colorize_client import _colorize_url, colorize_via_api


class ColorizeClientTests(SimpleTestCase):
    def test_successful_jpeg(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.headers = {"Content-Type": "image/jpeg"}
        mock_response.content = b"\xff\xd8fake-jpeg"

        with patch("genview.colorize_client.open", mock_open(read_data=b"img")), patch(
            "genview.colorize_client.requests.post", return_value=mock_response
        ):
            result = colorize_via_api("/tmp/photo.jpg")

        self.assertIsNone(result["error"])
        self.assertEqual(result["image"], b"\xff\xd8fake-jpeg")
        self.assertEqual(result["content_type"], "image/jpeg")

    def test_json_error_in_success_response(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.content = b"{}"
        mock_response.json.return_value = {"detail": "Model unavailable"}

        with patch("genview.colorize_client.open", mock_open(read_data=b"img")), patch(
            "genview.colorize_client.requests.post", return_value=mock_response
        ):
            result = colorize_via_api("/tmp/photo.jpg")

        self.assertEqual(result["error"], "Model unavailable")
        self.assertEqual(result["image"], b"")

    def test_http_error_uses_fastapi_detail(self):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.text = '{"detail":"DDColor-Fehler: oom"}'
        mock_response.json.return_value = {"detail": "DDColor-Fehler: oom"}
        http_error = requests.exceptions.HTTPError(response=mock_response)
        mock_response.raise_for_status.side_effect = http_error

        with patch("genview.colorize_client.open", mock_open(read_data=b"img")), patch(
            "genview.colorize_client.requests.post", return_value=mock_response
        ):
            result = colorize_via_api("/tmp/photo.jpg")

        self.assertEqual(result["error"], "DDColor-Fehler: oom")

    def test_invalid_json_non_image(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.headers = {"Content-Type": "text/plain"}
        mock_response.content = b"nope"
        mock_response.text = "nope"
        mock_response.json.side_effect = json.JSONDecodeError("bad", "doc", 0)

        with patch("genview.colorize_client.open", mock_open(read_data=b"img")), patch(
            "genview.colorize_client.requests.post", return_value=mock_response
        ):
            result = colorize_via_api("/tmp/photo.jpg")

        self.assertEqual(result["error"], "Ungültige Bild-Antwort vom ColorNode-Server.")

    def test_file_not_found(self):
        with patch("genview.colorize_client.open", side_effect=FileNotFoundError):
            result = colorize_via_api("/missing/photo.jpg")

        self.assertIn("nicht gefunden", result["error"])

    def test_empty_image_path(self):
        result = colorize_via_api("   ")
        self.assertIn("Kein Bildpfad", result["error"])

    def test_connection_error(self):
        with patch("genview.colorize_client.open", mock_open(read_data=b"img")), patch(
            "genview.colorize_client.requests.post",
            side_effect=requests.exceptions.ConnectionError,
        ):
            result = colorize_via_api("/tmp/photo.jpg")

        self.assertIn("Verbindung", result["error"])

    def test_timeout(self):
        with patch("genview.colorize_client.open", mock_open(read_data=b"img")), patch(
            "genview.colorize_client.requests.post",
            side_effect=requests.exceptions.Timeout,
        ):
            result = colorize_via_api("/tmp/photo.jpg")

        self.assertIn("Zeitlimit", result["error"])

    def test_url_appends_colorize(self):
        self.assertTrue(_colorize_url().endswith("/colorize"))
