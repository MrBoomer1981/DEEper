# CLAUDE.md: proxy

`proxy/` is a Cloudflare Worker: the address through which the user's phone opens DEEper from Russia without a VPN. It holds no state and shares no code with the Python parts, and it deploys with wrangler, not with a push. The repository's rules for working in one part are in the root CLAUDE.md.

## Why it exists

From Russian networks Railway's edge address (69.46.46.46: every `*.up.railway.app` site and railway.com itself) does not open without a VPN, whatever the domain name, so a custom domain on Railway would not help. Cloudflare does open; checked 2026-10-20, `*.workers.dev` opens and `*.pages.dev` does not.

## What it does

- `worker.js` passes every request on to the app on Railway: phone → Cloudflare → Railway.
  - The incoming request is the init of the new one, so method, headers (a WebSocket upgrade too, for NiceGUI's pages) and body pass on as they are.
  - Redirects go back to the browser, not followed here. A `Location` that names the Railway address is rewritten to the Worker's own.
- `ORIGIN`, the app's Railway address, is a Worker secret. Keep it, and the Worker's own address, out of this public repository. The phone opens `deeper.<account subdomain>.workers.dev`; the user knows the address.
- `wrangler.toml`: the Worker's name (`deeper`), its entry point and `workers_dev = true`.

## Deploy and check

```bash
npx wrangler deploy                   # from this folder; wrangler is logged in on the user's Mac
npx wrangler secret put ORIGIN        # only when the Railway address changes
```

- The first time on a machine, `npx wrangler login` opens a browser for the user's Cloudflare account. The user signs in; never type their credentials.
- **Check from a Russian network.** If the machine runs a VPN, `curl --interface en0` (the Wi-Fi interface on a Mac) goes around it: `curl --interface en0 -s -o /dev/null -w '%{http_code} %{time_total}\n' https://<the Worker's address>/login`.
- Some new connections to Cloudflare's addresses took about 19 s from a Russian home network (2026-10-20), so time a request more than once before blaming the Worker.

## What the app must keep for it

- Relative redirects and links. An absolute URL built from the request's host names the Railway address, which does not open in Russia; the Worker only rewrites `Location` headers.
- Per-IP logic sees a Cloudflare address, not the phone's. The login rate limit is the one such place.
