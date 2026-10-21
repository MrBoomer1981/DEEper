# CLAUDE.md: proxy

`proxy/` is a Cloudflare Worker: the address through which the user's phone opens DEEper from Russia without a VPN. It holds no state and shares no code with the Python parts, and it deploys with wrangler, not with a push. `ru/` holds an archived alternative, a server in Russia, which is not deployed (see the end of this file). The repository's rules for working in one part are in the root CLAUDE.md.

## Why it exists

From Russian networks Railway's edge address (69.46.46.46: every `*.up.railway.app` site and railway.com itself) does not open without a VPN, whatever the domain name, so a custom domain on Railway would not help. Cloudflare does open; checked 2026-10-20, `*.workers.dev` opens and `*.pages.dev` does not.

## What it does

- `worker.js` passes every request on to the app on Railway: phone → Cloudflare → Railway.
  - The incoming request is the init of the new one, so method, headers (a WebSocket upgrade too, for NiceGUI's pages) and body pass on as they are.
  - Redirects go back to the browser, not followed here. A `Location` that names the Railway address is rewritten to the Worker's own.
- `ORIGIN`, the app's Railway address, is a Worker secret. Keep it, and the Worker's own address, out of this public repository. The phone opens `deeper.<account subdomain>.workers.dev`; the user knows the address.
- `wrangler.toml`: the Worker's name (`deeper`), its entry point and `workers_dev = true`.

## Where it does not open: mobile internet

Since June 2025 Russian providers (Rostelecom, MegaFon, Beeline, MTS, MGTS) let a connection to many Cloudflare addresses carry only its first ~16 KB.
- The Worker's addresses (104.21.x, 172.67.x) are throttled this way on mobile internet. On 2026-10-20, from the iPhone's hotspot, a page froze after ~3 KB of body over HTTP/1.1 and before any byte over HTTP/2, while www.cloudflare.com (104.16.x) loaded.
- The same Worker loads on some fixed networks: home Wi-Fi on 2026-10-20, the ITMO network on 2026-10-21.
- Nothing in the Worker or the app changes this, and which addresses are throttled changes without notice. The fix is an address in Russia: `ru/` below.

## Deploy and check

```bash
npx wrangler deploy                   # from this folder; wrangler is logged in on the user's Mac
npx wrangler secret put ORIGIN        # only when the Railway address changes
```

- The first time on a machine, `npx wrangler login` opens a browser for the user's Cloudflare account. The user signs in; never type their credentials.
- **Check from a Russian network.** If the machine runs a VPN, `curl --interface en0` (the Wi-Fi interface on a Mac) goes around it: `curl --interface en0 -s -o /dev/null -w '%{http_code} %{time_total}\n' https://<the Worker's address>/login`. A fixed network passing says nothing about mobile internet.
- Some new connections to Cloudflare's addresses took about 19 s from a Russian home network (2026-10-20), so time a request more than once before blaming the Worker.

## What the app must keep for it

- Relative redirects and links. An absolute URL built from the request's host names the Railway address, which does not open in Russia; the Worker only rewrites `Location` headers.
- Per-IP logic sees a Cloudflare address, not the phone's. The login rate limit is the one such place.

## Archived: a server in Russia (`ru/`)

Prepared on 2026-10-21 for mobile internet and archived by the user the same day; plan.md has the item under «Архив». Nothing of it runs anywhere.
- The idea: a small Russian server (RUVDS «Старт», 139 ₽ a month) whose address is not throttled. The phone opens `https://<its IP>`, and the server passes every request on to Railway directly, or through this Worker if Railway does not open from its data center.
- `ru/Caddyfile`:
  - a Let's Encrypt certificate for the IP address (short-lived only, 6 days, renewed by Caddy; needs Caddy 2.10.2+);
  - the first healthy upstream, checked every 30 s on `/static/manifest.webmanifest`, which must then stay public;
  - `Host` set to the upstream's own, and redirects naming either address made relative.
- `ru/setup.sh <IP> "<Railway address> <Worker address>"`, run as root on a fresh Ubuntu or Debian:
  - installs Caddy from its own repository;
  - turns on unattended security updates;
  - writes the addresses into `/etc/caddy/deeper.env`, out of this repository;
  - turns off password sign-in over SSH;
  - starts Caddy and waits for the certificate.
- Checked only on the Mac (Caddy 2.11.7 with a local certificate): the sign-in page, NiceGUI's WebSocket over both paths, and the switch to the Worker when the first upstream does not answer. Never run on a real server: whether Railway or Cloudflare opens from a Russian data center is unknown.
- To bring it back:
  1. The user creates the server with the SSH key `deeper-ru`, which lives on their Mac as `~/.ssh/deeper_ru`.
  2. `scp -i ~/.ssh/deeper_ru -r proxy/ru root@<IP>:deeper-ru`, then `ssh -i ~/.ssh/deeper_ru root@<IP> sh deeper-ru/setup.sh <IP> "<Railway address> <Worker address>"`.
  3. Check from the phone on mobile internet with the VPN off.
