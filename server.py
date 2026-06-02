#!/usr/bin/env python3
"""
Migratune — Serveur Flask local
Importe un CSV Spotify vers une playlist YouTube Music via une interface web.
Projet libre et open source — https://github.com/
"""

import os
import csv
import threading
import time
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder="static")
CORS(app)

# État global partagé
state = {
    "status": "idle",          # idle | auth_needed | running | done | error
    "tracks": [],              # liste des titres du CSV
    "results": [],             # résultats par titre
    "progress": 0,
    "total": 0,
    "playlist_id": None,
    "playlist_name": "",
    "errors": [],
    "log": [],
}

ytmusic = None  # instance YTMusic


# ─── Routes statiques ────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ─── API ─────────────────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    return jsonify({
        "status": state["status"],
        "progress": state["progress"],
        "total": state["total"],
        "results": state["results"],
        "playlist_id": state["playlist_id"],
        "playlist_name": state["playlist_name"],
        "errors": state["errors"][-10:],
        "log": state["log"][-5:],
    })


@app.route("/api/upload-csv", methods=["POST"])
def api_upload_csv():
    """Parse le CSV uploadé et renvoie un aperçu."""
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "Aucun fichier reçu"}), 400

    raw = file.read()
    try:
        content = raw.decode("utf-8-sig")
        # Détecte le Mojibake (octets UTF-8 réinterprétés en Latin-1 puis re-sauvés en UTF-8)
        # Signature typique : é → Ã©, è → Ã¨, à → Ã ,  ï → Ã¯, etc.
        if any(c in content for c in ("Ã©", "Ã¨", "Ã ", "Ã®", "Ã¯", "Ã»", "Ã´")):
            try:
                content = content.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
    except UnicodeDecodeError:
        content = raw.decode("latin-1")
    reader = csv.DictReader(content.splitlines())
    tracks = []
    for row in reader:
        titre = row.get("Titre") or row.get("Track Name") or row.get("title") or ""
        artiste = row.get("Artiste") or row.get("Artist Name(s)") or row.get("artist") or ""
        album = row.get("Album") or row.get("album") or ""
        if titre:
            tracks.append({"title": titre.strip(), "artist": artiste.strip(), "album": album.strip()})

    state["tracks"] = tracks
    state["results"] = []
    state["progress"] = 0
    state["total"] = len(tracks)
    state["status"] = "idle"
    state["errors"] = []
    state["log"] = []

    return jsonify({
        "count": len(tracks),
        "preview": tracks[:5],
        "columns_detected": list(reader.fieldnames or []),
    })


def normalize_headers(raw: str) -> str:
    """Convertit le format Chrome DevTools (nom/valeur sur lignes alternées) en 'nom: valeur'."""
    lines = [l.rstrip() for l in raw.splitlines() if l.strip()]
    # Déjà au format "nom: valeur" si au moins la moitié des lignes contiennent ": "
    if sum(1 for l in lines if ": " in l) > len(lines) / 2:
        return raw
    # Format alterné : ligne paire = nom, ligne impaire = valeur
    result = []
    i = 0
    while i < len(lines) - 1:
        name = lines[i].strip()
        value = lines[i + 1].strip()
        if name.startswith(":"):   # pseudo-headers HTTP/2, ignorés
            i += 2
            continue
        result.append(f"{name}: {value}")
        i += 2
    return "\n".join(result)


@app.route("/api/auth", methods=["POST"])
def api_auth():
    """Authentification via headers de navigateur."""
    global ytmusic
    try:
        from ytmusicapi import YTMusic
        import ytmusicapi as ytm
        creds_path = "browser.json"

        data = request.json or {}
        headers_raw = normalize_headers(data.get("headers_raw", "").strip())

        if os.path.exists(creds_path) and not headers_raw:
            ytmusic = YTMusic(creds_path)
            state["status"] = "idle"
            log("✅ Connecté avec les identifiants existants.")
            return jsonify({"ok": True, "message": "Connecté avec browser.json existant"})

        if not headers_raw:
            return jsonify({"error": "Colle tes headers de navigateur dans le champ."}), 400

        ytm.setup(filepath=creds_path, headers_raw=headers_raw)
        ytmusic = YTMusic(creds_path)
        state["status"] = "idle"
        log("✅ Authentification réussie !")
        return jsonify({"ok": True, "message": "Connecté !"})

    except ImportError:
        return jsonify({"error": "ytmusicapi non installé. Lance : pip install ytmusicapi"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/start", methods=["POST"])
def api_start():
    """Démarre l'import en arrière-plan."""
    global ytmusic
    if not ytmusic:
        return jsonify({"error": "Non authentifié. Clique d'abord sur 'Connecter'."}), 400
    if not state["tracks"]:
        return jsonify({"error": "Aucun CSV chargé."}), 400
    if state["status"] == "running":
        return jsonify({"error": "Import déjà en cours."}), 400

    data = request.json or {}
    playlist_name = data.get("playlist_name", "Ma playlist Spotify").strip() or "Ma playlist Spotify"
    state["playlist_name"] = playlist_name
    state["status"] = "running"
    state["results"] = []
    state["progress"] = 0
    state["errors"] = []

    threading.Thread(target=run_import, args=(playlist_name,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    state["status"] = "idle"
    log("⏹ Import arrêté par l'utilisateur.")
    return jsonify({"ok": True})


# ─── Import en arrière-plan ───────────────────────────────────────────────────

def log(msg):
    print(msg)
    state["log"].append(msg)
    if len(state["log"]) > 200:
        state["log"] = state["log"][-200:]


def add_in_batches(playlist_id: str, video_ids: list, batch_size: int = 25):
    """Ajoute les IDs à la playlist par lots, avec retry sur erreur."""
    added = 0
    for i in range(0, len(video_ids), batch_size):
        batch = video_ids[i:i + batch_size]
        for attempt in range(3):
            try:
                ytmusic.add_playlist_items(playlist_id, batch, duplicates=True)
                added += len(batch)
                log(f"  ➕ Lot {i // batch_size + 1} : {len(batch)} titres ajoutés ({added} au total)")
                time.sleep(0.5)
                break
            except Exception as e:
                if attempt == 2:
                    log(f"  ❌ Lot {i // batch_size + 1} échoué après 3 tentatives : {e}")
                    state["errors"].append(f"Lot {i // batch_size + 1} non ajouté : {e}")
                else:
                    log(f"  ⚠️ Lot {i // batch_size + 1} erreur (tentative {attempt + 1}/3), retry…")
                    time.sleep(2 * (attempt + 1))
    return added


def run_import(playlist_name: str):
    global ytmusic
    try:
        log(f"📋 Création de la playlist « {playlist_name} »…")
        playlist_id = ytmusic.create_playlist(playlist_name, "Importée depuis Spotify via Migratune")
        state["playlist_id"] = playlist_id
        log(f"✅ Playlist créée : {playlist_id}")

        tracks = state["tracks"]
        state["total"] = len(tracks)
        video_ids = []

        # Phase 1 : recherche de tous les titres
        for i, track in enumerate(tracks):
            if state["status"] != "running":
                break

            query = f"{track['title']} {track['artist']}"
            result = {"title": track["title"], "artist": track["artist"], "status": "searching", "yt_title": ""}

            try:
                results = ytmusic.search(query, limit=1)
                if results:
                    top = results[0]
                    vid = top.get("videoId")
                    yt_title = top.get("title", "")
                    yt_artist = ", ".join(a["name"] for a in top.get("artists", []))
                    if vid:
                        video_ids.append(vid)
                        result["status"] = "found"
                        result["yt_title"] = f"{yt_title} — {yt_artist}"
                    else:
                        result["status"] = "no_id"
                else:
                    result["status"] = "not_found"
            except Exception as e:
                result["status"] = "error"
                result["error"] = str(e)
                state["errors"].append(f"{track['title']}: {e}")

            state["results"].append(result)
            state["progress"] = i + 1
            time.sleep(0.3)

        # Phase 2 : ajout en lots avec retry
        if video_ids and state["status"] == "running":
            log(f"🎵 Ajout de {len(video_ids)} titres à la playlist…")
            added = add_in_batches(playlist_id, video_ids)
            found = sum(1 for r in state["results"] if r["status"] == "found")
            state["status"] = "done"
            log(f"🎉 Import terminé ! {found} titres trouvés, {added} ajoutés à la playlist.")
        else:
            found = sum(1 for r in state["results"] if r["status"] == "found")
            state["status"] = "done"
            log(f"🎉 Import terminé ! {found}/{state['total']} titres trouvés.")

    except Exception as e:
        state["status"] = "error"
        state["errors"].append(str(e))
        log(f"❌ Erreur fatale : {e}")


# ─── Lancement ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.makedirs("static", exist_ok=True)
    print("\n" + "="*55)
    print("  🎵 YouTube Music Importer")
    print("  Ouvre http://localhost:5000 dans ton navigateur")
    print("="*55 + "\n")
    app.run(debug=False, port=5000)
