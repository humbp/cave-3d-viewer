# Cave 3D Viewer

Visionneuse 3D de topographies de cavités pour Android (Kivy + Buildozer).
Au lancement, une cavité synthétique (puits, galerie, salle, affluent, second puits) est affichée.

## Obtenir l'APK (GitHub Actions)

1. Créer un dépôt GitHub et y pousser tout le contenu de ce dossier.
2. Onglet **Actions** → workflow **Build APK** (il démarre à chaque push, ou via « Run workflow »).
3. À la fin (compter 20 à 40 min au premier passage), télécharger l'artefact **cave3dviewer-apk**,
   le dézipper et installer le `.apk` sur la tablette.

## Utilisation

| Geste | Effet |
|---|---|
| 1 doigt | rotation orbitale (ou panoramique si le bouton « 1 doigt » est sur panoramique) |
| 2 doigts | pincement = zoom, déplacement = panoramique |
| double tape | réinitialise la vue |

Barre d'outils : Ouvrir · Exemple · Calques · Couleur (profondeur / altitude / calques / uniforme) ·
Plan · Coupe · Vue 3D · mode 1 doigt · Stations · Infos (étendue X/Y/Z, dénivelé, développement,
sommets, segments, stations, avertissements).

## Formats lus

- **DXF** (`ezdxf`) : POINT, LINE, LWPOLYLINE, POLYLINE 2D/3D, ARC, CIRCLE, ELLIPSE, SPLINE, blocs INSERT, regroupés par calque. Unités converties en mètres. Le DWG n'est pas lu (exporter en DXF).
- **SVG** (`svgpathtools`, avec lecteur intégré de secours) : posé dans le plan X,Y ou dans la coupe X,Z, avec une échelle en mètres par unité SVG.
- **Texte topo** (`.txt .top .dat .xyz .tab`) :

```
@name Ma cavité
@unit m
@layer galerie
A 0 0 0            # station avec coordonnées : NOM X Y Z
B 10 0 -2
A B                # liaison
S0 S1 12.5 90 -10  # visée : DE VERS LONGUEUR AZIMUT PENTE
```

## Tests sur ordinateur

```
pip install -r requirements-dev.txt
python -m pytest tests
python main.py
```

## Structure

`parsers/` lecture des fichiers · `geometry/` modèle, caméra, couleurs · `rendering/` widget 3D ·
`ui/` interface et sélecteur de fichier Android · `main.py` · `buildozer.spec`.

## Notes de build

- Les dépendances d'`ezdxf` et de `svgpathtools` sont listées explicitement dans `requirements` car
  python-for-android installe les paquets Python purs sans leurs dépendances.
- `scipy` n'est pas compilable ici : si `svgpathtools` échoue à l'import, le lecteur SVG intégré le remplace
  (les arcs sont alors approchés par des segments droits et les `transform` ignorés).
- Aucune permission de stockage : les fichiers passent par le sélecteur système Android.
