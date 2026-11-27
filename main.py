"""Cave 3D Viewer : visionneuse 3D de topographies souterraines (Kivy)."""
from kivy.config import Config

Config.set("kivy", "exit_on_escape", "0")  # sur Android, Retour ne doit pas quitter brutalement

from kivy.app import App  # noqa: E402
from kivy.core.window import Window  # noqa: E402
from kivy.logger import Logger  # noqa: E402

from rendering.cave_view import BACKGROUND  # noqa: E402
from ui import dialogs  # noqa: E402
from ui.root import RootLayout  # noqa: E402


class CaveViewerApp(App):
    title = "Cave 3D Viewer"

    def build(self):
        Window.clearcolor = BACKGROUND
        Window.bind(on_keyboard=self._on_keyboard)
        self.root_widget = RootLayout()
        return self.root_widget

    def on_start(self):
        # Cavité synthétique : l'application est immédiatement testable
        self.root_widget.load_sample()

    def on_pause(self):
        return True  # garde l'application en vie quand le sélecteur de fichiers est ouvert

    def on_resume(self):
        pass

    def _on_keyboard(self, window, key, *args):
        if key == 27 and dialogs.close_top_popup():  # Retour Android / Échap
            return True
        return False


if __name__ == "__main__":
    try:
        CaveViewerApp().run()
    except Exception:
        Logger.exception("CaveViewer: arrêt sur erreur non gérée")
        raise
