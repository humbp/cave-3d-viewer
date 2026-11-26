[app]
title = Cave 3D Topo
package.name = cave3dtopo
package.domain = org.speleo.cave3dtopo

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,txt,pdf,dxf,svg

version = 0.1.0
version.code = 1

requirements = python3,kivy,numpy,ezdxf,svgpathtools

orientation = all
fullscreen = 0

android.permissions = READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,READ_MEDIA_DOCUMENTS
android.api = 34
android.minapi = 24
android.ndk = 25b
android.accept_sdk_license = True
android.archs = arm64-v8a

p4a.branch = master

[buildozer]
log_level = 2
warn_on_root = 1
