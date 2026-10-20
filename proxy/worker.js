// DEEper's address for networks where Railway does not open.
//
// From Russian networks Railway's edge address (69.46.46.46: every *.up.railway.app site and railway.com
// itself) does not open, whatever the domain name, while Cloudflare does. This Worker passes every request
// on to the app on Railway, the WebSocket of NiceGUI's pages included: phone -> Cloudflare -> Railway.
// ORIGIN, the app's Railway address, is a Worker secret (npx wrangler secret put ORIGIN), kept out of this
// public repository.

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = new URL(env.ORIGIN);
    const target = new URL(url.pathname + url.search, origin);
    // The incoming request is the init: method, headers (a WebSocket upgrade too) and body pass on as they are.
    // Redirects go back to the browser rather than being followed here.
    const response = await fetch(new Request(target, request), { redirect: "manual" });
    const location = response.headers.get("Location");
    if (!location || !location.startsWith(origin.origin)) {
      return response;
    }
    // A redirect naming the Railway address would send the browser back to where it does not open.
    const headers = new Headers(response.headers);
    headers.set("Location", url.origin + location.slice(origin.origin.length));
    return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
  },
};
