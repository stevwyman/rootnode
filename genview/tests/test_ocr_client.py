import json
from unittest.mock import MagicMock, mock_open, patch

from django.test import SimpleTestCase
import requests

from genview.ocr_client import extract_text_via_api


class OcrClientTests(SimpleTestCase):
    def test_successful_string_text(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"text": "Hello World"}

        with patch("genview.ocr_client.open", mock_open(read_data=b"img")), patch(
            "genview.ocr_client.requests.post", return_value=mock_response
        ):
            result = extract_text_via_api("/tmp/doc.jpg")

        self.assertIsNone(result["error"])
        self.assertEqual(result["text"], "Hello World")

    def test_successful_list_text(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"text": ["Line one", "Line two"]}

        with patch("genview.ocr_client.open", mock_open(read_data=b"img")), patch(
            "genview.ocr_client.requests.post", return_value=mock_response
        ):
            result = extract_text_via_api("/tmp/doc.jpg")

        self.assertIsNone(result["error"])
        self.assertEqual(result["text"], "Line one\nLine two")

    def test_list_of_dict_lines(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "text": [{"text": "First"}, {"content": "Second"}]
        }

        with patch("genview.ocr_client.open", mock_open(read_data=b"img")), patch(
            "genview.ocr_client.requests.post", return_value=mock_response
        ):
            result = extract_text_via_api("/tmp/doc.jpg")

        self.assertEqual(result["text"], "First\nSecond")

    def test_api_error_in_success_response(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"error": "Unsupported format"}

        with patch("genview.ocr_client.open", mock_open(read_data=b"img")), patch(
            "genview.ocr_client.requests.post", return_value=mock_response
        ):
            result = extract_text_via_api("/tmp/doc.jpg")

        self.assertEqual(result["error"], "Unsupported format")
        self.assertEqual(result["text"], "")

    def test_success_false_in_payload(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"success": False, "message": "Engine offline"}

        with patch("genview.ocr_client.open", mock_open(read_data=b"img")), patch(
            "genview.ocr_client.requests.post", return_value=mock_response
        ):
            result = extract_text_via_api("/tmp/doc.jpg")

        self.assertEqual(result["error"], "Engine offline")

    def test_invalid_json_response(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.side_effect = json.JSONDecodeError("bad", "doc", 0)
        mock_response.text = "not-json"

        with patch("genview.ocr_client.open", mock_open(read_data=b"img")), patch(
            "genview.ocr_client.requests.post", return_value=mock_response
        ):
            result = extract_text_via_api("/tmp/doc.jpg")

        self.assertEqual(result["error"], "Ungültige JSON-Antwort vom OCR-Server.")

    def test_invalid_text_type_returns_error(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"text": {"unexpected": "object"}}

        with patch("genview.ocr_client.open", mock_open(read_data=b"img")), patch(
            "genview.ocr_client.requests.post", return_value=mock_response
        ):
            result = extract_text_via_api("/tmp/doc.jpg")

        self.assertIn("text", result["error"])

    def test_empty_text_is_success(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"text": "   "}

        with patch("genview.ocr_client.open", mock_open(read_data=b"img")), patch(
            "genview.ocr_client.requests.post", return_value=mock_response
        ):
            result = extract_text_via_api("/tmp/doc.jpg")

        self.assertIsNone(result["error"])
        self.assertEqual(result["text"], "   ")

    def test_http_error_returns_sanitized_message(self):
        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.text = "<html>" + ("x" * 1000) + "</html>"
        http_error = requests.exceptions.HTTPError(response=mock_response)
        mock_response.raise_for_status.side_effect = http_error

        with patch("genview.ocr_client.open", mock_open(read_data=b"img")), patch(
            "genview.ocr_client.requests.post", return_value=mock_response
        ):
            result = extract_text_via_api("/tmp/doc.jpg")

        self.assertEqual(result["error"], "OCR-Server antwortete mit HTTP 502.")
        self.assertNotIn("<html>", result["error"])

    def test_file_not_found(self):
        with patch("genview.ocr_client.open", side_effect=FileNotFoundError):
            result = extract_text_via_api("/missing/doc.jpg")

        self.assertIn("nicht gefunden", result["error"])

    def test_empty_image_path(self):
        result = extract_text_via_api("   ")
        self.assertIn("Kein Bildpfad", result["error"])

    def test_connection_error(self):
        with patch("genview.ocr_client.open", mock_open(read_data=b"img")), patch(
            "genview.ocr_client.requests.post",
            side_effect=requests.exceptions.ConnectionError,
        ):
            result = extract_text_via_api("/tmp/doc.jpg")

        self.assertIn("Verbindung", result["error"])

    def test_timeout(self):
        with patch("genview.ocr_client.open", mock_open(read_data=b"img")), patch(
            "genview.ocr_client.requests.post",
            side_effect=requests.exceptions.Timeout,
        ):
            result = extract_text_via_api("/tmp/doc.jpg")

        self.assertIn("Zeitlimit", result["error"])
