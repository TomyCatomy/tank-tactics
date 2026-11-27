// Trusted Types: the page's Content-Security-Policy makes the browser refuse plain strings
// for script URLs and HTML. This is the one policy allowed to create them, and the only
// thing it permits is loading Google's sign-in library. Everything else is built as DOM
// nodes with h() / sv(), which needs no policy.
export const GOOGLE_GSI_CLIENT = 'https://accounts.google.com/gsi/client';

const policy = window.trustedTypes?.createPolicy('tank-tactics', {
  createScriptURL(url) {
    if (url === GOOGLE_GSI_CLIENT) return url;
    throw new TypeError(`Refusing to load script ${url}`);
  },
});

/** A script URL the browser will accept under Trusted Types (a plain string where unsupported). */
export const scriptURL = (url) => (policy ? policy.createScriptURL(url) : url);
