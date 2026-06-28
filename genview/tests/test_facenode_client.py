import json
from unittest.mock import MagicMock, mock_open, patch

from django.test import SimpleTestCase
import requests

from genview.facenode_client import detect_faces_via_api


class FaceNodeClientTests(SimpleTestCase):
    def test_successful_detection(self):
        payload = {
            "faces": [
                {
                    "x": 10,
                    "y": 20,
                    "width": 100,
                    "height": 120,
                    "confidence": 0.95,
                    "embedding": [0.1, 0.2],
                }
            ]
        }
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = payload

        with patch("genview.facenode_client.open", mock_open(read_data=b"img")), patch(
            "genview.facenode_client.requests.post", return_value=mock_response
        ):
            result = detect_faces_via_api("/tmp/photo.jpg")

        self.assertIsNone(result["error"])
        self.assertEqual(len(result["faces"]), 1)
        self.assertEqual(result["faces"][0]["width"], 100)
        self.assertEqual(result["faces"][0]["embedding"], [0.1, 0.2])

    def test_api_error_in_success_response(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"error": "No face detected", "faces": None}

        with patch("genview.facenode_client.open", mock_open(read_data=b"img")), patch(
            "genview.facenode_client.requests.post", return_value=mock_response
        ):
            result = detect_faces_via_api("/tmp/photo.jpg")

        self.assertEqual(result["error"], "No face detected")
        self.assertEqual(result["faces"], [])

    def test_success_false_in_payload(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"success": False, "message": "Model unavailable"}

        with patch("genview.facenode_client.open", mock_open(read_data=b"img")), patch(
            "genview.facenode_client.requests.post", return_value=mock_response
        ):
            result = detect_faces_via_api("/tmp/photo.jpg")

        self.assertEqual(result["error"], "Model unavailable")

    def test_invalid_json_response(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.side_effect = json.JSONDecodeError("bad", "doc", 0)
        mock_response.text = "not-json"

        with patch("genview.facenode_client.open", mock_open(read_data=b"img")), patch(
            "genview.facenode_client.requests.post", return_value=mock_response
        ):
            result = detect_faces_via_api("/tmp/photo.jpg")

        self.assertEqual(result["error"], "Ungültige JSON-Antwort vom FaceNode-Server.")

    def test_malformed_face_entry_is_skipped(self):
        payload = {
            "faces": [
                {"x": "bad", "y": 0, "width": 10, "height": 10},
                {"x": 1, "y": 2, "width": 10, "height": 10, "confidence": 0.8},
            ]
        }
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = payload

        with patch("genview.facenode_client.open", mock_open(read_data=b"img")), patch(
            "genview.facenode_client.requests.post", return_value=mock_response
        ):
            result = detect_faces_via_api("/tmp/photo.jpg")

        self.assertIsNone(result["error"])
        self.assertEqual(len(result["faces"]), 1)
        self.assertEqual(result["faces"][0]["x"], 1)

    def test_faces_not_a_list_returns_error(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"faces": "nope"}

        with patch("genview.facenode_client.open", mock_open(read_data=b"img")), patch(
            "genview.facenode_client.requests.post", return_value=mock_response
        ):
            result = detect_faces_via_api("/tmp/photo.jpg")

        self.assertIn("faces", result["error"])

    def test_http_error_returns_sanitized_message(self):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "<html>" + ("x" * 1000) + "</html>"
        http_error = requests.exceptions.HTTPError(response=mock_response)
        mock_response.raise_for_status.side_effect = http_error

        with patch("genview.facenode_client.open", mock_open(read_data=b"img")), patch(
            "genview.facenode_client.requests.post", return_value=mock_response
        ):
            result = detect_faces_via_api("/tmp/photo.jpg")

        self.assertEqual(result["error"], "FaceNode-Server antwortete mit HTTP 500.")
        self.assertNotIn("<html>", result["error"])

    def test_file_not_found(self):
        with patch("genview.facenode_client.open", side_effect=FileNotFoundError):
            result = detect_faces_via_api("/missing/photo.jpg")

        self.assertIn("nicht gefunden", result["error"])

    def test_empty_image_path(self):
        result = detect_faces_via_api("   ")
        self.assertIn("Kein Bildpfad", result["error"])

    def test_connection_error(self):
        with patch("genview.facenode_client.open", mock_open(read_data=b"img")), patch(
            "genview.facenode_client.requests.post",
            side_effect=requests.exceptions.ConnectionError,
        ):
            result = detect_faces_via_api("/tmp/photo.jpg")

        self.assertIn("Verbindung", result["error"])

    def test_timeout(self):
        with patch("genview.facenode_client.open", mock_open(read_data=b"img")), patch(
            "genview.facenode_client.requests.post",
            side_effect=requests.exceptions.Timeout,
        ):
            result = detect_faces_via_api("/tmp/photo.jpg")

        self.assertIn("Zeitlimit", result["error"])
