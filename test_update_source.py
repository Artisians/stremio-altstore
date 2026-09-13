import hashlib
import io
import json
import plistlib
import tempfile
import unittest
import zipfile
from pathlib import Path

import update_source


def make_ipa(bundle="com.stremio.pal", version="2.0.7", build="22"):
    info = {
        "CFBundleIdentifier": bundle,
        "CFBundleShortVersionString": version,
        "CFBundleVersion": build,
        "CFBundleSupportedPlatforms": ["iPhoneOS"],
        "UIDeviceFamily": [1, 2],
        "MinimumOSVersion": "13.0",
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("Payload/Stremio.app/Info.plist", plistlib.dumps(info))
    return output.getvalue()


def make_official(version="2.0.7", build="22"):
    return json.dumps({
        "apps": [{
            "bundleIdentifier": "com.stremio.pal",
            "versions": [{
                "version": version,
                "buildVersion": build,
                "date": "2026-08-24",
                "minOSVersion": "13.0",
                "localizedDescription": "A verified update.",
            }],
        }],
    }).encode()


class UpdateSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "stremio-ios.json"
        self.source.write_text(json.dumps({
            "apps": [{
                "bundleIdentifier": "com.stremio.pal",
                "iconURL": update_source.ICON_URL,
                "versions": [{"version": "2.0.6", "buildVersion": "21"}],
            }],
            "news": [],
        }))

    def tearDown(self):
        self.temp.cleanup()

    def test_updates_only_after_ipa_identity_checks(self):
        ipa = make_ipa()

        def fetch(url, limit):
            return make_official() if url == update_source.OFFICIAL_SOURCE_URL else ipa

        self.assertTrue(update_source.update_source(self.source, fetch))
        data = json.loads(self.source.read_text())
        latest = data["apps"][0]["versions"][0]
        self.assertEqual(latest["version"], "2.0.7")
        self.assertEqual(latest["sha256"], hashlib.sha256(ipa).hexdigest())
        self.assertEqual(data["apps"][0]["iconURL"], update_source.ICON_URL)
        self.assertTrue(data["news"][0]["notify"])
        self.assertEqual(data["news"][0]["imageURL"], update_source.ICON_URL)

    def test_selects_latest_official_version_regardless_of_order(self):
        source = json.loads(make_official("2.1.0", "3"))
        source["apps"][0]["versions"] += [
            {"version": "2.0.10", "buildVersion": "99"},
            {"version": "2.1.0", "buildVersion": "2"},
        ]

        latest = update_source.official_latest(json.dumps(source).encode())

        self.assertEqual((latest["version"], latest["buildVersion"]), ("2.1.0", "3"))

    def test_rejects_wrong_bundle(self):
        def fetch(url, limit):
            return make_official() if url == update_source.OFFICIAL_SOURCE_URL else make_ipa(bundle="example.evil")

        with self.assertRaisesRegex(ValueError, "CFBundleIdentifier"):
            update_source.update_source(self.source, fetch)

    def test_current_version_does_not_download_ipa(self):
        urls = []

        def fetch(url, limit):
            urls.append(url)
            return make_official("2.0.6", "21")

        before = self.source.read_bytes()
        self.assertFalse(update_source.update_source(self.source, fetch))
        self.assertEqual(urls, [update_source.OFFICIAL_SOURCE_URL])
        self.assertEqual(self.source.read_bytes(), before)

    def test_rejects_nonofficial_urls(self):
        for url in ("http://dl.strem.io/a", "https://evil.example/a", "https://dl.strem.io.evil.example/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                update_source.require_official_url(url)


if __name__ == "__main__":
    unittest.main()
