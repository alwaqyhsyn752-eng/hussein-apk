[app]
title = Hussein
package.name = hussein
package.domain = org.hussein
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json
version = 3.0.0
requirements = python3,kivy==2.3.0,aiohttp,beautifulsoup4,certifi
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,ACCESS_NETWORK_STATE
android.api = 31
android.minapi = 21
android.ndk = 25b
android.archs = arm64-v8a
android.allow_backup = True
android.accept_sdk_license = True
icon.filename = %(source.dir)s/icon.png
presplash.filename = %(source.dir)s/presplash.png

[buildozer]
log_level = 2
warn_on_root = 1
