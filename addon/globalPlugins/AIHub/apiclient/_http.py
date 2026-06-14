"""Low-level HTTP helpers shared by all REST endpoints.

Uses only Python standard library so the addon does not need to bundle
the openai/anthropic SDKs into NVDA's runtime.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Any, Optional

from ._errors import APIConnectionError, APIStatusError, _resolve_error_message

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


def _create_opener():
	"""Create a urllib opener with default SSL context."""
	ctx = ssl.create_default_context()
	return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))


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


def _build_anthropic_headers(api_key: str) -> dict:
	"""Headers for Anthropic Messages API (x-api-key + version pin)."""
	return {
		"x-api-key": api_key,
		"anthropic-version": "2023-06-01",
		"Content-Type": "application/json",
		"User-Agent": _USER_AGENT,
	}


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
		)
	except (urllib.error.URLError, OSError, ConnectionError) as e:
		raise APIConnectionError(str(e)) from e
	if resp.status != 200:
		text = resp.read().decode("utf-8", errors="replace")
		try:
			resp.close()
		except Exception:
			pass
		raise APIStatusError(
			_resolve_error_message(text, resp.status),
			status_code=resp.status,
			response_body=text,
		)
	return resp


def _open_json(opener, req, *, timeout: int) -> dict:
	"""Open a request and return the parsed JSON body. Always closes the response."""
	try:
		with opener.open(req, timeout=timeout) as resp:
			raw = resp.read().decode("utf-8", errors="replace")
			if resp.status != 200:
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
				raise APIStatusError(
					_resolve_error_message(raw, resp.status),
					status_code=resp.status,
					response_body=raw,
				)
	except urllib.error.HTTPError as e:
		text = e.read().decode("utf-8", errors="replace") if e.fp else ""
		raise APIStatusError(
			_resolve_error_message(text, e.code),
			status_code=e.code,
			response_body=text,
		)
	except (urllib.error.URLError, OSError, ConnectionError) as e:
		raise APIConnectionError(str(e)) from e


def _open_bytes(opener, req, *, timeout: int) -> tuple[bytes, str]:
	"""Open a request and return (raw_bytes, content_type). Always closes the response."""
	try:
		with opener.open(req, timeout=timeout) as resp:
			if resp.status != 200:
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
		)
	except (urllib.error.URLError, OSError, ConnectionError) as e:
		raise APIConnectionError(str(e)) from e
