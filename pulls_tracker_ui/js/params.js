/* Page parameters. window.PT_PARAMS holds the parameters of the address, from the query string (?state=synced, used in a browser)
   and from the fragment (#backend=1&data=..., used by the application). The application cannot use a query string: the Windows
   WebView2 host turns "?" in a file address into part of the file name and answers "File not found". */
window.PT_PARAMS = (function () {
  'use strict';
  const p = new URLSearchParams(location.search);
  new URLSearchParams(location.hash.replace(/^#/, '')).forEach((v, k) => p.set(k, v));
  return p;
})();
