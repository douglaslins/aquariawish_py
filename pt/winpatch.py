"""Changes to how pywebview drives WebView2 on Windows.

pywebview (6.x) subscribes to every web resource request and every response of the page and runs a Python function for each one, on the
window thread: it builds a dictionary of all the headers and raises its request_sent / response_received events. Nothing in this
application listens to those events. The page loads fonts, icons and banner art from files, so each screen change would stop the window
thread for a Python call per file. This module removes that: the request filter is removed (WebView2 then does not raise the request
event at all) and the two handlers do nothing.

Safe to call more than once. It does nothing outside Windows. Setting the environment variable PULLS_TRACKER_NO_PATCH=1 turns it off
(to find out whether it is the cause of a problem).
"""
from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger("pt.winpatch")


def _nothing(self, sender=None, args=None) -> None:
    return None


def patch_edge_chromium(module) -> bool:
    """Patch the pywebview module `module` (webview.platforms.edgechromium). True when it was patched now."""
    cls = getattr(module, "EdgeChrome", None)
    if cls is None or getattr(cls, "_pt_patched", False):
        return False
    original = cls.on_webview_ready
    cls.on_web_resource_request = _nothing
    cls.on_web_resource_response = _nothing

    def on_webview_ready(self, sender, args):
        original(self, sender, args)
        if not getattr(args, "IsSuccess", False):
            return
        core = sender.CoreWebView2
        context = getattr(module, "CoreWebView2WebResourceContext", None)
        for step in (lambda: core.RemoveWebResourceRequestedFilter("*", context.All),
                     lambda: core.WebResourceRequested.__isub__(self.on_web_resource_request),
                     lambda: core.WebResourceResponseReceived.__isub__(self.on_web_resource_response)):
            try:
                step()
            except Exception:       # the handlers above do nothing anyway
                log.debug("WebView2 handler removal step failed", exc_info=True)

    cls.on_webview_ready = on_webview_ready
    cls._pt_patched = True
    return True


def apply() -> bool:
    if sys.platform != "win32" or os.environ.get("PULLS_TRACKER_NO_PATCH"):
        return False
    try:
        from webview.platforms import edgechromium
    except Exception:
        log.debug("pywebview has no WebView2 module here", exc_info=True)
        return False
    try:
        return patch_edge_chromium(edgechromium)
    except Exception:       # a pywebview 6.x built differently: the window works without the patch (only slower), so the start goes on
        log.warning("The WebView2 patch could not be applied; pywebview is used as it is", exc_info=True)
        return False
