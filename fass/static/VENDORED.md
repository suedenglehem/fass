# Vendored player library

Pinned upstream release, served read-only by `GET /static/{name}`
(api.py allowlist — this is the only name it will serve).

| file                       | upstream | license |
|----------------------------|----------|---------|
| `shaka-player.compiled.js` | npm `shaka-player@5.3.2` → `dist/shaka-player.compiled.js` (global `shaka`; UMD build with no UI, `new shaka.Player(video)`) | Apache-2.0, see `shaka-player.compiled.js.LICENSE.txt` |

Shaka Player plays both HLS (`.m3u8`) and DASH (`.mpd`) through one API, so
it replaces the earlier hls.js + dash.js pair.

Re-verify after any re-download: `sha256sum -c SHA256SUMS`.
To upgrade: bump the version here + in `api.py`'s static allowlist
docs, re-download, re-hash.
