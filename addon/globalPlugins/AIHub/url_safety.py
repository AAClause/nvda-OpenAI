"""Validation for user-supplied HTTP(S) URLs (SSRF / unsafe scheme mitigation)."""

from __future__ import annotations

import ipaddress
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

# Cloud metadata endpoints commonly targeted by SSRF (AWS / Azure / GCP instance
# metadata, and AWS's IPv6 one).
_BLOCKED_METADATA = frozenset({ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254")})


def _is_blocked(address: str) -> bool:
	try:
		ip = ipaddress.ip_address(address.split("%")[0])
	except ValueError:
		return False
	# ::ffff:169.254.169.254 reaches the IPv4 address.
	mapped = getattr(ip, "ipv4_mapped", None)
	return ip in _BLOCKED_METADATA or mapped in _BLOCKED_METADATA


def validate_http_fetch_url(url: str) -> None:
	"""
	Raise ValueError if ``url`` must not be fetched (unsupported scheme or blocked host).

	Intended for URLs pasted by the user (e.g. image-from-URL). Blocks the well-known
	instance metadata address on resolved IPs. Validates each redirect target via
	:class:`ValidatedRedirectHandler` when using :func:`build_http_fetch_opener`.
	"""
	if not isinstance(url, str) or not url.strip():
		raise ValueError("empty URL")
	parsed = urllib.parse.urlsplit(url.strip())
	if parsed.scheme not in ("http", "https"):
		raise ValueError("only http and https URLs are allowed")
	host = parsed.hostname
	if not host:
		raise ValueError("missing hostname")
	_check_host_and_resolve(host)


def _check_host_and_resolve(host: str) -> None:
	if _is_blocked(host.strip().strip("[]").lower()):
		raise ValueError("blocked host")
	try:
		addresses = [res[4][0] for res in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]
	except socket.gaierror as e:
		raise ValueError("could not resolve hostname") from e
	if any(_is_blocked(addr) for addr in addresses):
		raise ValueError("blocked host")


class ValidatedRedirectHandler(urllib.request.HTTPRedirectHandler):
	"""Follow redirects only if each target passes :func:`validate_http_fetch_url`."""

	max_redirections = 10

	def redirect_request(self, req, fp, code, msg, headers, newurl):
		try:
			validate_http_fetch_url(newurl)
		except ValueError as e:
			raise urllib.error.URLError(f"Redirect blocked: {e}") from None
		return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_http_fetch_opener() -> urllib.request.OpenerDirector:
	"""Opener with TLS defaults and validated redirects (for user-supplied URLs)."""
	ctx = ssl.create_default_context()
	return urllib.request.build_opener(
		ValidatedRedirectHandler,
		urllib.request.HTTPHandler(),
		urllib.request.HTTPSHandler(context=ctx),
	)
