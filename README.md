# Mihomo — personal stable builds

Personal distribution based on [MetaCubeX/mihomo](https://github.com/MetaCubeX/mihomo). [Download a signed application or a stable core from Releases](https://github.com/vertno/mihomo/releases). The release tag points at the complete patched source. The `distribution` branch contains the downstream patch and release controls.

XHTTP nodes may set `xhttp-opts.download-settings.h2-stream-receive-window` in bytes for their independent HTTP/2 download transport. Omitting it or specifying zero uses the upstream default. For a 16 MiB window use `16777216`.
