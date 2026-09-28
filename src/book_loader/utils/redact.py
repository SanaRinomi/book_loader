"""
Redaction helpers for log output.

Keeps what is useful for diagnosing network problems (scheme, host, generic
path words, query parameter names, file extensions, header names) and hides
what identifies the user or the book (tokens, signatures, IDs, titles, IPs,
emails, cookies).
"""

import re
from urllib.parse import urlsplit

# Query parameters whose values are generic and useful for debugging.
SAFE_QUERY_KEYS = {"output", "source", "hl", "format", "type"}

# Adobe Content Server endpoint names (mixed case, so they fail the generic check).
SAFE_PATH_SEGMENTS = {
    "Fulfill",
    "Notify",
    "LoanReturn",
    "Auth",
    "InitLicenseService",
    "AuthenticationServiceInfo",
    "SignInDirect",
    "Activate",
}

# Plain lowercase path words such as "books", "download", "fulfillment", "acs4".
_SAFE_SEGMENT_RE = re.compile(r"^[a-z][a-z0-9-]{0,15}$")

# Headers whose values are always hidden.
SECRET_HEADERS = {"set-cookie", "cookie", "authorization", "proxy-authorization"}

# XML elements (any namespace prefix) whose text identifies the user, device or book.
_SENSITIVE_TAGS = (
    "user|userId|device|fingerprint|deviceKey|signature|hmac|nonce|transaction|loan|"
    "username|encryptedKey|privateLicenseKey|pkcs12|certificate|licenseCertificate|"
    "authenticationCertificate|title|creator|publisher|identifier|description"
)

_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_TAG_RE = re.compile(
    r"(<(?:[\w-]+:)?(?:%s)\b[^>]*(?<!/)>)([^<]+)(</)" % _SENSITIVE_TAGS, re.IGNORECASE
)
_LONG_NUMBER_RE = re.compile(r"\b\d{12,}\b")  # numeric account IDs, e.g. Google user IDs
_VERSION_SEGMENT_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){0,2}$")  # e.g. "1.1" in namespace URLs
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6_RE = re.compile(
    r"\b(?:[0-9a-fA-F]{1,4}:){3,7}[0-9a-fA-F]{1,4}\b"  # full form
    r"|(?<![\w:])(?:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4})*)?::"  # compressed form (::)
    r"(?:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4})*)?(?![\w:])"
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9+/_-]{24,}={0,2}")


def _mask(value: str) -> str:
    return "<hidden:%d>" % len(value) if value else value


def _redact_segment(segment: str) -> str:
    if (
        not segment
        or segment in SAFE_PATH_SEGMENTS
        or _SAFE_SEGMENT_RE.match(segment)
        or _VERSION_SEGMENT_RE.match(segment)
    ):
        return segment
    stem, dot, ext = segment.rpartition(".")
    if dot and stem and ext.isalnum() and len(ext) <= 5:
        return "%s.%s" % (_mask(stem), ext)
    return _mask(segment)


def redact_url(url: str) -> str:
    """Hide identifying parts of a URL, keeping its overall shape."""
    if not url:
        return url
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return "<hidden url>"

    out = ""
    if parts.scheme:
        out += parts.scheme + "://"
    out += host
    if port:
        out += ":%d" % port
    out += "/".join(_redact_segment(s) for s in parts.path.split("/"))
    if parts.query:
        # URLs embedded in XML use "&amp;" as the separator.
        sep = "&amp;" if "&amp;" in parts.query else "&"
        pairs = [p.partition("=") for p in parts.query.split(sep)]
        out += "?" + sep.join(
            k + eq + (v if k.lower() in SAFE_QUERY_KEYS else _mask(v)) for k, eq, v in pairs
        )
    if parts.fragment:
        out += "#" + _mask(parts.fragment)
    return out


def redact_text(text: str) -> str:
    """Hide identifying data in free-form text (XML payloads, HTML error pages, headers)."""
    if not text:
        return text
    # Redact URLs first and set them aside, so the generic passes below
    # don't re-mask the parts redact_url deliberately kept (host, path words).
    urls = []

    def _stash(m):
        urls.append(redact_url(m.group(0)))
        return "\x00%d\x00" % (len(urls) - 1)

    text = _URL_RE.sub(_stash, text)
    text = _TAG_RE.sub(lambda m: m.group(1) + _mask(m.group(2)) + m.group(3), text)
    text = _UUID_RE.sub("<uuid>", text)
    text = _EMAIL_RE.sub("<email>", text)
    text = _IPV4_RE.sub("<ip>", text)
    text = _IPV6_RE.sub("<ip>", text)
    text = _LONG_NUMBER_RE.sub(lambda m: _mask(m.group(0)), text)
    text = _TOKEN_RE.sub(lambda m: _mask(m.group(0)), text)
    return re.sub("\x00(\\d+)\x00", lambda m: urls[int(m.group(1))], text)


def redact_header(name: str, value: str) -> str:
    """Redact a single HTTP header value."""
    if name.lower() in SECRET_HEADERS:
        return _mask(value)
    if name.lower() in ("location", "content-location", "referer"):
        return redact_url(value)
    return redact_text(value)
