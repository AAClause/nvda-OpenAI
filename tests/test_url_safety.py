import socket
import urllib.error
import urllib.request

import pytest

from AIHub import url_safety
from AIHub.url_safety import ValidatedRedirectHandler, validate_http_fetch_url

_DNS = {
	"example.com": "93.184.215.14",
	"metadata.example.com": "169.254.169.254",
	"mapped.example.com": "::ffff:169.254.169.254",
}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
	def getaddrinfo(host, port, *args, **kwargs):
		address = _DNS.get(host, host)
		try:
			family = socket.AF_INET6 if ":" in address else socket.AF_INET
			socket.inet_pton(family, address)
		except OSError:
			raise socket.gaierror(f"unknown host {host}") from None
		return [(family, socket.SOCK_STREAM, 6, "", (address, 0))]

	monkeypatch.setattr(url_safety.socket, "getaddrinfo", getaddrinfo)


@pytest.mark.parametrize("url", [
	"https://example.com/cat.png",
	"http://93.184.215.14/cat.png",
	"https://[2606:2800:21f:cb07:6820:80da:af6b:8b2c]/cat.png",
])
def test_public_urls_are_allowed(url):
	validate_http_fetch_url(url)


@pytest.mark.parametrize("url", [
	"http://169.254.169.254/latest/meta-data/",
	"http://[::ffff:169.254.169.254]/latest/meta-data/",
	"http://[::ffff:a9fe:a9fe]/latest/meta-data/",
	"http://[fd00:ec2::254]/latest/meta-data/",
	# Host names resolving to the metadata address.
	"http://metadata.example.com/",
	"http://mapped.example.com/",
])
def test_metadata_endpoints_are_blocked(url):
	with pytest.raises(ValueError, match="blocked host"):
		validate_http_fetch_url(url)


@pytest.mark.parametrize("url, reason", [
	("", "empty URL"),
	("file:///C:/Windows/win.ini", "only http and https"),
	("ftp://example.com/a.png", "only http and https"),
	("https:///a.png", "missing hostname"),
	("https://no-such-host.invalid/a.png", "could not resolve"),
])
def test_invalid_urls_are_rejected(url, reason):
	with pytest.raises(ValueError, match=reason):
		validate_http_fetch_url(url)


def test_redirect_to_metadata_endpoint_is_refused():
	req = urllib.request.Request("https://example.com/cat.png")
	with pytest.raises(urllib.error.URLError, match="Redirect blocked"):
		ValidatedRedirectHandler().redirect_request(req, None, 302, "Found", {}, "http://169.254.169.254/")
