# Stremio AltStore source

Source URL:

`https://artisians.github.io/stremio-altstore/stremio-ios.json`

The hourly workflow watches Stremio's official source. It publishes an update only after the IPA:

- downloads from HTTPS on `dl.strem.io`;
- matches `com.stremio.pal`, the advertised version, build, and iPhone platform;
- matches the advertised minimum iOS version;
- receives a SHA-256 hash that AltStore checks before installation.

The source hosts a copy of Stremio's official icon so the listing does not depend on an external image URL remaining available.

When the source changes, AltStore can show the new version under **My Apps**. Updating over Wi-Fi requires AltServer to be running on the paired Mac.

This is an unofficial source and is not affiliated with Stremio. The IPA remains hosted by Stremio.
