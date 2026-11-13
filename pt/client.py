"""Requests to the game's pull history service (rebuild analysis section 3).

The history is read page by page, newest first: 20 items a page, the next page asks for items older than the last id of this one
(`end_id`), and the list is empty after the oldest pull. About 0.6 s are left between requests. The service answers `retcode` 0 or an
error with a `message`; the reference program matches the message text (so does this one, and the two retcode numbers commonly seen,
-100 and -101 for the key, -110 for "too frequently" [U]).

Nothing here writes the address or the key to a log. `http` and `sleep` are parameters so tests do not need a network or time.
"""
from __future__ import annotations

import http.client
import json
import logging
import re
import socket
import threading
import urllib.error
import urllib.request
from typing import Callable, Iterator, Optional

from pt import locate
from pt.games import Game

log = logging.getLogger("pt.client")

PAGE_SIZE = 20
PAUSE = 0.6                 # seconds between pages
PAGE_TIMEOUT = 10.0         # seconds for one page
CHECK_TIMEOUT = 8.0         # seconds for the test of an address (the servers are far away from some players: 5 s was too short now and then)
CHECK_TRIES = 2             # tries of the test of an address when the network fails or the server has a fault (HTTP 5xx or 429)
CHECK_RETRY_PAUSE = 1.5     # seconds between two tries
RATE_WAIT = 15.0            # seconds to wait after "too frequently"
RATE_RETRIES = 5
NET_RETRIES = 2             # more tries of a page after a network error (2 s apart)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PullsTracker"
MAX_BODY = 8 * 1024 * 1024


class Problem(Exception):
    """A failure the screen can explain. `code` is the code the page knows (js/views.js ERRORS)."""
    code = "server_error"

    def __init__(self, message: str = "", **params):
        super().__init__(message)
        self.message = message
        self.params = params


class NetworkError(Problem):
    code = "network"


class LinkExpired(Problem):
    code = "link_expired"


class RateLimited(Problem):
    code = "rate_limited"


class ApiError(Problem):
    code = "server_error"


class Cancelled(Exception):
    """The user cancelled the sync."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The history service does not redirect. Following one would send the request to a host the allowlist (locate.allowed_host) never
    checked, so a redirect is returned as its status code (an ApiError) instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):      # noqa: ARG002 - the signature of the method it replaces
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def urllib_get(url: str, timeout: float) -> tuple[int, bytes]:
    if not url.startswith("https://"):
        raise NetworkError("only https addresses are requested")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})      # noqa: S310 - https only, checked above
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            body = resp.read(MAX_BODY)
            expected = resp.headers.get("Content-Length")
            if expected and expected.isdigit() and len(body) < min(int(expected), MAX_BODY):      # the connection ended before the answer did
                raise NetworkError("the answer was cut short")
            return resp.status, body
    except urllib.error.HTTPError as e:
        e.close()
        return e.code, b""
    except (socket.timeout, TimeoutError) as e:
        raise NetworkError("the server did not answer in time") from e
    except urllib.error.URLError as e:
        raise NetworkError(str(getattr(e, "reason", e))) from e
    except http.client.HTTPException as e:      # IncompleteRead, BadStatusLine ...: not OSErrors, so without this they are not retried.
        raise NetworkError(f"the answer was not complete ({type(e).__name__})") from e      # Only the type: InvalidURL's text holds the address
    except OSError as e:
        raise NetworkError(str(e)) from e


def interpret(status: int, body: bytes) -> dict:
    """The `data` of a good answer. Raises LinkExpired, RateLimited or ApiError for an answer that says otherwise."""
    if status != 200:
        raise ApiError(f"HTTP {status}", status=status)
    try:
        doc = json.loads(body.decode("utf-8", "replace"))
    except ValueError as e:
        raise ApiError("the answer is not JSON") from e
    if not isinstance(doc, dict):
        raise ApiError("the answer is not a JSON object")
    code, message = doc.get("retcode"), str(doc.get("message") or "")
    if code == 0:
        data = doc.get("data")
        return data if isinstance(data, dict) else {}
    low = message.lower()
    if "frequently" in low or code == -110:
        raise RateLimited(message, retcode=code)
    # Genshin says "authkey timeout", Zenless "auth key time out" (with another code than -100/-101): the letters are compared without spaces
    if "authkey" in re.sub(r"[^a-z]", "", low) or code in (-100, -101):
        raise LinkExpired(message, retcode=code)
    raise ApiError(message or f"retcode {code}", retcode=code)


class Client:
    def __init__(self, game: Game, link: locate.Link, host: str, *, http: Callable = urllib_get, sleep: Optional[Callable[[float], None]] = None,
                 cancel: Optional[threading.Event] = None, pause: float = PAUSE, rate_wait: float = RATE_WAIT, lang: Optional[str] = "en"):
        self.game, self.link, self.host, self.http = game, link, host, http
        self.cancel = cancel or threading.Event()
        self._sleep = sleep or self.cancel.wait
        self.pause, self.rate_wait = pause, rate_wait
        # Item names come in the language of the request. The banner data is English, so the request asks for English. If the service
        # refuses that, the address' own language is used from then on (the names then may not be English: `names_english` is False).
        self.lang = lang if (lang and (link.value("lang") or "").lower() not in ("en", "en-us")) else None
        self.names_english = self.lang is not None or (link.value("lang") or "en").lower() in ("en", "en-us")

    # ---- one request -----------------------------------------------------------------------------------------------------------

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()

    def _wait(self, seconds: float) -> None:
        self._sleep(seconds)
        self._check_cancel()

    def _url(self, code: str, page: int, end_id: str, size: int) -> str:
        params = {"gacha_type": code, "size": str(size), "page": str(page), "end_id": end_id}
        for extra in self.game.request_extra:
            params[extra] = code
        if self.lang:
            params["lang"] = self.lang
        return locate.request_url(self.game, self.link, self.host, params)

    def _get(self, url: str, timeout: float) -> dict:
        return interpret(*self.http(url, timeout))

    def _request(self, code: str, page: int, end_id: str, size: int, timeout: float, on_wait: Optional[Callable[[dict], None]] = None,
                 retries: bool = True) -> dict:
        rate_tries = net_tries = 0
        while True:
            self._check_cancel()
            try:
                try:
                    return self._get(self._url(code, page, end_id, size), timeout)
                except LinkExpired:
                    if not self.lang:
                        raise
                    log.warning("The service refused the language override; the language of the saved link is used")
                    self.lang, self.names_english = None, False
                    return self._get(self._url(code, page, end_id, size), timeout)
            except RateLimited:
                rate_tries += 1
                if not retries or rate_tries > RATE_RETRIES:
                    raise
                if on_wait:
                    on_wait({"sec": int(self.rate_wait), "retry": rate_tries, "of": RATE_RETRIES, "stop": rate_tries == RATE_RETRIES})
                self._wait(self.rate_wait)
            except NetworkError:
                net_tries += 1
                if not retries or net_tries > NET_RETRIES:
                    raise
                self._wait(2.0)

    # ---- uses ------------------------------------------------------------------------------------------------------------------

    def check(self) -> dict:
        """Test the link with a one-item request for the game's first banner type. Returns the answer's data; raises Problem. A network error or
        a server fault is tried once more: one lost request must not end the sync (the sync stops when nothing answers at the first address).
        An answer about the key (expired, too frequent) or HTTP 404 is final: asking again would give the same answer."""
        for attempt in range(1, CHECK_TRIES + 1):
            try:
                return self._request(self.game.groups[0].request, 1, "0", 1, CHECK_TIMEOUT, retries=False)
            except NetworkError:
                if attempt == CHECK_TRIES:
                    raise
            except ApiError as e:
                status = e.params.get("status") or 0
                if attempt == CHECK_TRIES or not (status >= 500 or status == 429):
                    raise
            self._wait(CHECK_RETRY_PAUSE)
        raise AssertionError("unreachable")      # the loop returns or raises on its last try

    def pages(self, code: str, on_wait: Optional[Callable[[dict], None]] = None) -> Iterator[tuple[int, list, dict]]:
        """(page number, items, the page's data) of one banner type, newest first, until the service has no more."""
        end_id, number = "0", 0
        seen = {end_id}                  # the last id of every page so far: a service that repeats one would otherwise be asked for ever
        while True:
            number += 1
            data = self._request(code, number, end_id, PAGE_SIZE, PAGE_TIMEOUT, on_wait)
            items = data.get("list") or []
            if not items:
                return
            if not isinstance(items, list) or not isinstance(items[-1], dict):
                raise ApiError("the list of items is not in the expected form")
            yield number, items, data
            end_id = str(items[-1].get("id") or "")
            if not end_id:
                raise ApiError("an item has no id")
            if end_id in seen:
                raise ApiError("the service sent the same page again")
            seen.add(end_id)
            self._wait(self.pause)
