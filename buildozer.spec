[app]
title = Cave 3D Viewer
package.name = cave3dviewer
package.domain = org.cavetools

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,txt,dxf,svg
source.exclude_dirs = tests,bin,.github,.buildozer,__pycache__
version = 1.0.0

# Dépendances. python-for-android installe les paquets Python purs SANS leurs
# dépendances : les dépendances d'ezdxf (pyparsing, typing_extensions, fonttools)
# et de svgpathtools (svgwrite) sont donc listées explicitement.
# scipy n'est pas compilable ici : le lecteur SVG intégré prend le relais si
# svgpathtools échoue à l'import.
requirements = python3,kivy,numpy,ezdxf,svgpathtools,pyparsing,typing_extensions,fonttools,svgwrite

orientation = landscape, portrait
fullscreen = 0

# Aucune permission de stockage : les fichiers sont ouverts via le sélecteur
# système (Storage Access Framework).
android.permissions =

android.archs = arm64-v8a
android.api = 34
android.minapi = 24
android.accept_sdk_license = True
android.enable_androidx = True
android.logcat_filters = *:S python:D

[buildozer]
log_level = 2
warn_on_root = 1
