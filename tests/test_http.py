import http.server
import threading
import urllib.request

import pytest

from AIHub.apiclient._http import _create_opener


class _Handler(http.server.BaseHTTPRequestHandler):
	def do_GET(self):
		self.server.seen.append((self.path, dict(self.headers)))
		target = self.server.redirects.get(self.path)
		if target:
			self.send_response(302)
			self.send_header("Location", target)
			self.end_headers()
			return
		self.send_response(200)
		self.send_header("Content-Length", "2")
		self.end_headers()
		self.wfile.write(b"ok")

	def log_message(self, *args):
		pass


@pytest.fixture
def make_server():
	servers = []

	def make():
		server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
		server.seen, server.redirects = [], {}
		threading.Thread(target=server.serve_forever, daemon=True).start()
		servers.append(server)
		return server, f"http://127.0.0.1:{server.server_port}"

	yield make
	for server in servers:
		server.shutdown()
		server.server_close()


def _get(url):
	headers = {"Authorization": "Bearer secret", "x-api-key": "secret", "x-goog-api-key": "secret", "Accept": "*/*"}
	with _create_opener().open(urllib.request.Request(url, headers=headers), timeout=5) as response:
		return response.read()


def test_redirect_to_another_origin_drops_credentials(make_server):
	api, api_url = make_server()
	other, other_url = make_server()
	api.redirects["/v1/models"] = other_url + "/collect"
	assert _get(api_url + "/v1/models") == b"ok"
	_path, headers = other.seen[0]
	assert {k.lower() for k in headers} & {"authorization", "x-api-key", "x-goog-api-key"} == set()
	assert headers["Accept"] == "*/*"


def test_same_origin_redirect_keeps_credentials(make_server):
	api, api_url = make_server()
	api.redirects["/v1/models"] = "/v2/models"
	assert _get(api_url + "/v1/models") == b"ok"
	path, headers = api.seen[1]
	assert path == "/v2/models"
	assert headers["Authorization"] == "Bearer secret"
