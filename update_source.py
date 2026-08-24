#!/usr/bin/env python3

import hashlib
import io
import json
import plistlib
import re
import sys
import zipfile
from datetime import date
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

OFFICIAL_SOURCE_URL = "https://dl.strem.io/apple/altstore/source.json"
EXPECTED_HOST = "dl.strem.io"
EXPECTED_BUNDLE = "com.stremio.pal"
MAX_SOURCE_BYTES = 1_000_000
MAX_IPA_BYTES = 500 * 1024 * 1024
MAX_PLIST_BYTES = 1_000_000
VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def require_official_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != EXPECTED_HOST:
        raise ValueError(f"Refusing non-official URL: {url}")


class OfficialRedirectsOnly(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require_official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = build_opener(OfficialRedirectsOnly)


def fetch_bytes(url, limit):
    require_official_url(url)
    request = Request(url, headers={"User-Agent": "Stremio-AltStore-Source/1"})
    with OPENER.open(request, timeout=60) as response:
        require_official_url(response.geturl())
        length = response.headers.get("Content-Length")
        if length and int(length) > limit:
            raise ValueError(f"Response exceeds {limit} bytes")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"Response exceeds {limit} bytes")
    return data


def version_key(entry):
    version = entry.get("version")
    build = entry.get("buildVersion")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise ValueError(f"Invalid version: {version!r}")
    if not isinstance(build, str) or not build.isdigit():
        raise ValueError(f"Invalid buildVersion: {build!r}")
    return tuple(map(int, version.split("."))), int(build)


def official_latest(raw):
    data = json.loads(raw)
    apps = [app for app in data.get("apps", []) if app.get("bundleIdentifier") == EXPECTED_BUNDLE]
    if len(apps) != 1 or not apps[0].get("versions"):
        raise ValueError("Official source does not contain exactly one Stremio app with versions")
    return max(apps[0]["versions"], key=version_key)


def inspect_ipa(raw, expected):
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as error:
        raise ValueError("Downloaded file is not an IPA ZIP archive") from error

    with archive:
        plists = []
        for name in archive.namelist():
            parts = PurePosixPath(name).parts
            if len(parts) == 3 and parts[0] == "Payload" and parts[1].endswith(".app") and parts[2] == "Info.plist":
                plists.append(name)
        if len(plists) != 1:
            raise ValueError("IPA must contain exactly one main app Info.plist")
        if archive.getinfo(plists[0]).file_size > MAX_PLIST_BYTES:
            raise ValueError("IPA Info.plist is too large")
        info = plistlib.loads(archive.read(plists[0]))
    required = {
        "CFBundleIdentifier": EXPECTED_BUNDLE,
        "CFBundleShortVersionString": expected["version"],
        "CFBundleVersion": expected["buildVersion"],
    }
    for key, value in required.items():
        if str(info.get(key)) != value:
            raise ValueError(f"IPA {key} is {info.get(key)!r}, expected {value!r}")
    if "iPhoneOS" not in info.get("CFBundleSupportedPlatforms", []):
        raise ValueError("IPA does not support iPhoneOS")
    if not set(info.get("UIDeviceFamily", [])).intersection({1, 2}):
        raise ValueError("IPA does not support iPhone or iPad")
    return info


def update_source(source_path, fetch=fetch_bytes):
    source = json.loads(source_path.read_text(encoding="utf-8"))
    apps = source.get("apps", [])
    if len(apps) != 1 or apps[0].get("bundleIdentifier") != EXPECTED_BUNDLE:
        raise ValueError("Local source must contain only com.stremio.pal")
    app = apps[0]
    current = max(app.get("versions", []), key=version_key)

    latest = official_latest(fetch(OFFICIAL_SOURCE_URL, MAX_SOURCE_BYTES))
    if version_key(latest) <= version_key(current):
        print(f"No update: Stremio {current['version']} build {current['buildVersion']} is current")
        return False

    version = latest["version"]
    build = latest["buildVersion"]
    ipa_url = f"https://{EXPECTED_HOST}/apple/{version}b{build}/ios/stremio_iOS.ipa"
    ipa = fetch(ipa_url, MAX_IPA_BYTES)
    info = inspect_ipa(ipa, latest)

    release_date = latest.get("date")
    if not isinstance(release_date, str):
        raise ValueError("Official release date is missing")
    date.fromisoformat(release_date)
    min_os = latest.get("minOSVersion")
    if not isinstance(min_os, str) or not VERSION_RE.fullmatch(min_os):
        raise ValueError("Official minOSVersion is invalid")
    if str(info.get("MinimumOSVersion")) != min_os:
        raise ValueError("IPA MinimumOSVersion does not match the official source")

    description = latest.get("localizedDescription") or f"Stremio {version} build {build}."
    if not isinstance(description, str) or len(description) > 6000 or CONTROL_RE.search(description):
        raise ValueError("Official release description is invalid")

    new_version = {
        "version": version,
        "buildVersion": build,
        "date": release_date,
        "localizedDescription": description,
        "downloadURL": ipa_url,
        "size": len(ipa),
        "sha256": hashlib.sha256(ipa).hexdigest(),
        "minOSVersion": min_os,
    }
    app["versions"] = [new_version] + [
        item for item in app["versions"] if version_key(item) != version_key(new_version)
    ]
    app.update({
        "version": version,
        "versionDate": release_date,
        "versionDescription": description,
        "downloadURL": ipa_url,
        "size": len(ipa),
        "minOSVersion": min_os,
    })
    source["news"] = [{
        "title": f"Stremio {version} is available",
        "identifier": f"stremio-{version.replace('.', '-')}-b{build}",
        "caption": description.splitlines()[0][:200],
        "tintColor": "7055D9",
        "date": release_date,
        "notify": True,
        "appID": EXPECTED_BUNDLE,
        "imageURL": app["iconURL"],
    }]
    source_path.write_text(json.dumps(source, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Updated to Stremio {version} build {build}; sha256={new_version['sha256']}")
    return True


if __name__ == "__main__":
    try:
        update_source(Path(sys.argv[1] if len(sys.argv) > 1 else "stremio-ios.json"))
    except Exception as error:
        print(f"Update refused: {error}", file=sys.stderr)
        raise SystemExit(1)
