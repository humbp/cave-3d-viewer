"""Sélecteur de fichier Android via le Storage Access Framework (SAF).

Sur Android 11+ (stockage cloisonné) l'application ne peut pas parcourir librement
/sdcard : on demande donc le fichier au sélecteur système (ACTION_OPEN_DOCUMENT), puis on
en copie le contenu dans le dossier privé de l'application. Aucune permission de stockage
n'est nécessaire. Les modules ``jnius`` et ``android`` n'existent que sur Android :
ils sont importés à l'intérieur des fonctions.
"""
from __future__ import annotations

import os
import re
import shutil
from typing import Callable, Optional

from kivy.clock import Clock
from kivy.logger import Logger
from kivy.utils import platform

REQUEST_CODE = 0x3D01
RESULT_OK = -1


def is_android() -> bool:
    return platform == "android"


def _display_name(resolver, uri) -> Optional[str]:
    try:
        cursor = resolver.query(uri, None, None, None, None)
        if cursor is not None:
            try:
                index = cursor.getColumnIndex("_display_name")
                if index >= 0 and cursor.moveToFirst():
                    return cursor.getString(index)
            finally:
                cursor.close()
    except Exception:
        Logger.warning("CaveViewer: nom du fichier SAF introuvable", exc_info=True)
    return None


def _copy_uri_to_cache(activity_obj, uri, cache_dir: str) -> str:
    resolver = activity_obj.getContentResolver()
    name = _display_name(resolver, uri) or "import.dat"
    safe = re.sub(r"[^\w.\-]+", "_", name) or "import.dat"
    os.makedirs(cache_dir, exist_ok=True)
    destination = os.path.join(cache_dir, safe)
    descriptor = resolver.openFileDescriptor(uri, "r")
    fd = descriptor.detachFd()  # la propriété du descripteur passe à Python
    with os.fdopen(fd, "rb") as source, open(destination, "wb") as target:
        shutil.copyfileobj(source, target, 1024 * 1024)
    return destination


def pick_file_android(on_path: Callable[[Optional[str]], None],
                      on_error: Callable[[str], None],
                      cache_dir: str) -> None:
    """Ouvre le sélecteur système. ``on_path(None)`` si l'utilisateur annule."""
    try:
        from android import activity
        from jnius import autoclass

        python_activity = autoclass("org.kivy.android.PythonActivity")
        intent_class = autoclass("android.content.Intent")
    except Exception as exc:
        message = f"Sélecteur de fichiers Android indisponible : {exc}"
        Clock.schedule_once(lambda dt, m=message: on_error(m), 0)
        return

    def on_result(request_code, result_code, intent):
        if request_code != REQUEST_CODE:
            return
        try:
            activity.unbind(on_activity_result=on_result)
        except Exception:
            pass
        if result_code != RESULT_OK or intent is None:
            Clock.schedule_once(lambda dt: on_path(None), 0)
            return
        try:
            path = _copy_uri_to_cache(python_activity.mActivity, intent.getData(), cache_dir)
        except Exception as exc:
            Logger.exception("CaveViewer: copie du fichier choisi impossible")
            message = f"Impossible de lire le fichier choisi : {exc}"
            Clock.schedule_once(lambda dt, m=message: on_error(m), 0)
            return
        Clock.schedule_once(lambda dt, p=path: on_path(p), 0)

    try:
        activity.bind(on_activity_result=on_result)
        intent = intent_class(intent_class.ACTION_OPEN_DOCUMENT)
        intent.addCategory(intent_class.CATEGORY_OPENABLE)
        intent.setType("*/*")
        python_activity.mActivity.startActivityForResult(intent, REQUEST_CODE)
    except Exception as exc:
        message = f"Ouverture du sélecteur impossible : {exc}"
        Clock.schedule_once(lambda dt, m=message: on_error(m), 0)
