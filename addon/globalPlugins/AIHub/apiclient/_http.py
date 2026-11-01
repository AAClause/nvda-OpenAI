"""Low-level HTTP helpers shared by all REST endpoints.

Uses only Python standard library so the addon does not need to bundle
the openai/anthropic SDKs into NVDA's runtime.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from ._errors import APIConnectionError, APIStatusError, _resolve_error_message
from logHandler import log

# User-Agent crafted to look like a regular browser so Cloudflare-fronted
# providers (notably api.x.ai) don't 1010-block stdlib's "Python-urllib/..." UA.
_USER_AGENT = (
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
	"(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_AUDIO_MIME = {
	".wav": "audio/wav",
	".mp3": "audio/mpeg",
	".m4a": "audio/mp4",
	".webm": "audio/webm",
	".mp4": "audio/mp4",
	".flac": "audio/flac",
	".ogg": "audio/ogg",
	".mpga": "audio/mpeg",
	".mpeg": "audio/mpeg",
}


# Request headers that carry credentials (lower case).
_CREDENTIAL_HEADERS = frozenset({"authorization", "x-api-key", "x-goog-api-key", "openai-organization"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> tuple:
	parts = urllib.parse.urlsplit(url)
	scheme = parts.scheme.lower()
	return scheme, (parts.hostname or "").lower(), parts.port or _DEFAULT_PORTS.get(scheme)


class _CredentialSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
	"""Follow redirects without sending credentials to another origin.

	urllib copies every request header to the redirect target, so an endpoint
	answering with a redirect to another host (or to plain http) would
	otherwise receive the API key.
	"""

	def redirect_request(self, req, fp, code, msg, headers, newurl):
		new = super().redirect_request(req, fp, code, msg, headers, newurl)
		if new is not None and _origin(newurl) != _origin(req.full_url):
			for name in [n for n in new.headers if n.lower() in _CREDENTIAL_HEADERS]:
				del new.headers[name]
		return new


def _create_opener():
	"""Create a urllib opener with default SSL context."""
	ctx = ssl.create_default_context()
	return urllib.request.build_opener(_CredentialSafeRedirectHandler, urllib.request.HTTPSHandler(context=ctx))


def _build_headers(api_key: str, organization: Optional[str] = None) -> dict:
	"""Headers for OpenAI-compatible (Bearer token) endpoints."""
	headers = {
		"Content-Type": "application/json",
		"User-Agent": _USER_AGENT,
	}
	if api_key and str(api_key).strip():
		headers["Authorization"] = f"Bearer {api_key}"
	if organization and organization.strip():
		org_val = organization.strip()
		# Tolerate accidentally-stored configspec serialised values like ":= ORG".
		if ":= " in org_val:
			org_val = org_val.split(":= ", 1)[-1]
		if org_val and org_val != ":=":
			headers["OpenAI-Organization"] = org_val
	return headers


# 2026-10-21 rejects temperature/top_p/top_k on every model:
# https://platform.claude.com/docs/en/api/migration-2026-10-21
ANTHROPIC_API_VERSION = "2026-10-21"


def _build_anthropic_headers(api_key: str, betas=()) -> dict:
	"""Headers for Anthropic Messages API (x-api-key + version pin, optional betas)."""
	headers = {
		"x-api-key": api_key,
		"anthropic-version": ANTHROPIC_API_VERSION,
		"Content-Type": "application/json",
		"User-Agent": _USER_AGENT,
	}
	if betas:
		headers["anthropic-beta"] = ",".join(betas)
	return headers


def _open_streaming(opener, req, *, timeout: int) -> Any:
	"""Open a request that will be consumed as a stream.

	Returns the response object directly (no `with` block) so the caller
	can keep reading after this function returns. Raises APIStatusError
	if the initial status is not 200.
	"""
	try:
		resp = opener.open(req, timeout=timeout)
	except urllib.error.HTTPError as e:
		text = e.read().decode("utf-8", errors="replace") if e.fp else ""
		raise APIStatusError(
			_resolve_error_message(text, e.code),
			status_code=e.code,
			response_body=text,
		) from None
	except (urllib.error.URLError, OSError, ConnectionError) as e:
		raise APIConnectionError(str(e)) from e
	if resp.status != 200:
		text = resp.read().decode("utf-8", errors="replace")
		try:
			resp.close()
		except Exception:
			log.debug("Ignored error", exc_info=True)
		raise APIStatusError(
			_resolve_error_message(text, resp.status),
			status_code=resp.status,
			response_body=text,
		)
	return resp


def _open_json(opener, req, *, timeout: int, allow_text: bool = False) -> Any:
	"""Open a request and return the parsed JSON body. Always closes the response.

	With ``allow_text``, a non-JSON body is returned as a string instead of
	raising.
	"""
	try:
		with opener.open(req, timeout=timeout) as resp:
			raw = resp.read().decode("utf-8", errors="replace")
			if not 200 <= resp.status < 300:
				raise APIStatusError(
					_resolve_error_message(raw, resp.status),
					status_code=resp.status,
					response_body=raw,
				)
			if not raw:
				return {}
			try:
				return json.loads(raw)
			except json.JSONDecodeError:
				if allow_text:
					return raw
				raise APIStatusError(
					_resolve_error_message(raw, resp.status),
					status_code=resp.status,
					response_body=raw,
				) from None
	except urllib.error.HTTPError as e:
		text = e.read().decode("utf-8", errors="replace") if e.fp else ""
		raise APIStatusError(
			_resolve_error_message(text, e.code),
			status_code=e.code,
			response_body=text,
		) from None
	except (urllib.error.URLError, OSError, ConnectionError) as e:
		raise APIConnectionError(str(e)) from e


def _open_bytes(opener, req, *, timeout: int) -> tuple[bytes, str]:
	"""Open a request and return (raw_bytes, content_type). Always closes the response."""
	try:
		with opener.open(req, timeout=timeout) as resp:
			if not 200 <= resp.status < 300:
				text = resp.read().decode("utf-8", errors="replace")
				raise APIStatusError(
					_resolve_error_message(text, resp.status),
					status_code=resp.status,
					response_body=text,
				)
			content_type = (resp.headers.get("Content-Type") or "").lower()
			return resp.read(), content_type
	except urllib.error.HTTPError as e:
		text = e.read().decode("utf-8", errors="replace") if e.fp else ""
		raise APIStatusError(
			_resolve_error_message(text, e.code),
			status_code=e.code,
			response_body=text,
		) from None
	except (urllib.error.URLError, OSError, ConnectionError) as e:
		raise APIConnectionError(str(e)) from e


def http_request_json(
	url: str,
	*,
	method: str = "GET",
	headers: Optional[dict] = None,
	json_body: Any = None,
	data: Optional[bytes] = None,
	timeout: int = 30,
	allow_text: bool = False,
) -> Any:
	"""Send one HTTP request and return its parsed JSON body (``{}`` when empty).

	Shared by every module that is not a chat client (model lists, tools,
	whisper.cpp) so they get the same User-Agent, proxy/SSL opener and error
	parsing. Raises ``APIStatusError`` for HTTP errors and
	``APIConnectionError`` for network failures.
	"""
	all_headers = {"User-Agent": _USER_AGENT}
	all_headers.update(headers or {})
	if json_body is not None:
		data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
		all_headers.setdefault("Content-Type", "application/json")
	req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
	return _open_json(_create_opener(), req, timeout=timeout, allow_text=allow_text)
