# Mihomo Stable Builds

Download the Windows amd64-v2 or ARM64 core and its version file from the [latest core release](https://github.com/vertno/mihomo/releases/latest).

A VLESS XHTTP node can set `xhttp-opts.download-settings.h2-stream-receive-window` in bytes for its independent HTTP/2 download connection. Use `16777216` for 16 MiB. Omitting the field or setting it to zero uses the upstream default.

The release tag contains the patched source and records the upstream commit in `distribution.lock.json`. The `distribution` branch holds the patch and release workflow. [Official Mihomo](https://github.com/MetaCubeX/mihomo)

Both the stable and alpha update channels resolve to the personal stable release.

Each official stable tag produces one release named `vX.Y.Z-patched`.
