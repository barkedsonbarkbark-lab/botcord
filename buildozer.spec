[app]
title = Botcord
package.name = botcord
package.domain = org.botcord.app
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 0.3
requirements = python3,kivy==2.3.0,kivymd==1.2.0,pillow,requests,urllib3,idna,charset-normalizer,certifi,websocket-client,openssl,cryptography,pyjnius
orientation = portrait
fullscreen = 0

android.permissions = INTERNET
android.api = 33
android.minapi = 24
android.ndk = 25b
android.accept_sdk_license = True
android.archs = arm64-v8a

[buildozer]
log_level = 2
warn_on_root = 1
