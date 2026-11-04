# Upstream credits

This project integrates and patches existing tools.

| Upstream | License | Role |
|---|---|---|
| [JuzzyDee/audio-analyzer-rs](https://github.com/JuzzyDee/audio-analyzer-rs) | MIT | DSP and short-clip patch |
| [rosasynthesiz/flstudio-mcp](https://github.com/rosasynthesiz/flstudio-mcp) | MIT | MIDI controller and recovery/duplex/global-index patches |
| [CryptoJones/FL-Studio-MCP-Server](https://github.com/CryptoJones/FL-Studio-MCP-Server) | Apache-2.0 | Workbench and Playlist patch |
| [whatanaiceguy/kuruitsugizaki](https://github.com/whatanaiceguy/kuruitsugizaki) | MIT | Pinned FLP codec |

Full licenses accompany the release in third-party-licenses/. install.ps1 pins source revisions. Patches modify the named projects. FL Studio, plugins and samples are separate proprietary products.

Patches retain the license of the upstream source they modify. MIT applies to original adapter code, not a relicensing of Apache-2.0 upstream content.
