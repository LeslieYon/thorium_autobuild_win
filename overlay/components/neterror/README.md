## Thorium Neterror Overlay&nbsp;&nbsp;<img src="https://github.com/Alex313031/thorium/blob/main/logos/NEW/components/200/error_network_generic.png" width="48">

This overlay carries Thorium-specific resources for Chromium's network error
page and offline dino game.

Keep Chromium-owned source files, such as `neterror.html` and
`dino_game/offline.ts`, as patches instead of copying them wholesale from this
directory. Those files change upstream and should be rebased against the current
Chromium implementation.

Binary artwork and audio for the Thorium dino game can remain as overlay
resources:

- `resources/images/default_100_percent/offline/*.png`
- `resources/images/default_200_percent/offline/*.png`
- `resources/sounds/perpetuum_factory_2.mp3`

---
See also:
- https://chromium.googlesource.com/chromium/src/+/main/components/neterror
- https://chromium.googlesource.com/chromium/src/+/main/components/security_interstitials/core/common/resources/
