#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import datetime as dt
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

# ---------- Utils ----------
def run(cmd, log, check=True):
    log.debug("RUN: %s", " ".join(map(str, cmd)))
    return subprocess.run(cmd, check=check)

def which(bin_name):
    return shutil.which(bin_name)

def ts_srt(t):
    # t in seconds -> "HH:MM:SS,mmm"
    ms = int(round((t - int(t)) * 1000))
    t = int(t)
    s = t % 60
    t //= 60
    m = t % 60
    h = t // 60
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def ts_vtt(t):
    # t in seconds -> "HH:MM:SS.mmm"
    ms = int(round((t - int(t)) * 1000))
    t = int(t)
    s = t % 60
    t //= 60
    m = t % 60
    h = t // 60
    return f"{h:02}:{m:02}:{s:02}.{ms:03}"

def write_txt(path: Path, segments):
    with path.open("w", encoding="utf-8") as f:
        for seg in segments:
            f.write(seg["text"].strip() + "\n")

def write_srt(path: Path, segments):
    with path.open("w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, start=1):
            f.write(str(i) + "\n")
            f.write(f"{ts_srt(seg['start'])} --> {ts_srt(seg['end'])}\n")
            f.write(seg["text"].strip() + "\n\n")

def write_vtt(path: Path, segments):
    with path.open("w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for seg in segments:
            f.write(f"{ts_vtt(seg['start'])} --> {ts_vtt(seg['end'])}\n")
            f.write(seg["text"].strip() + "\n\n")

def pbcopy(text, log):
    try:
        p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        p.communicate(input=text.encode("utf-8"))
    except Exception as e:
        log.warning("pbcopy échoué: %s", e)

def open_in_finder(folder, log):
    try:
        subprocess.run(["open", str(folder)], check=False)
    except Exception as e:
        log.warning("Ouverture Finder échouée: %s", e)

def normalize_media_path(raw):
    """Clean user/Shortcut provided path strings before Path()."""
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else None
        if raw is None:
            return None
    if isinstance(raw, Path):
        raw = str(raw)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    for sep in ("\x00", "\r", "\n"):
        if sep in raw:
            raw = raw.replace(sep, "")
    raw = "".join(ch for ch in raw if ch.isprintable())
    raw = raw.strip()
    if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
        raw = raw[1:-1]
    if raw.startswith("file://"):
        raw = unquote(raw[7:])
    raw = raw.replace("\\ ", " ")
    return raw or None

def pick_media_file():
    """Select an audio/video file via dialog when available, else via CLI prompt."""

    def prompt_cli():
        if not sys.stdin.isatty():
            print("Entrée standard non interactive: passe un fichier en argument (Shortcut → « Entrées : en arguments »).", file=sys.stderr)
            return None
        try:
            import shlex
            raw = input("Entrez le chemin du fichier (ou glissez-déposez puis Entrée): ").strip()
        except EOFError:
            return None
        if not raw:
            return None
        try:
            parsed = shlex.split(raw)
            if parsed:
                raw = parsed[0]
        except ValueError:
            pass
        return raw

    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as e:
        print(f"Sélection via GUI indisponible (tkinter manquant: {e})", file=sys.stderr)
        return normalize_media_path(prompt_cli())

    if os.environ.get("NO_TK") or os.environ.get("SHORTCUTS_RUNNER"):
        return normalize_media_path(prompt_cli())

    try:
        root = tk.Tk()
        root.withdraw()
        root.update()
        filetypes = [
            ("Médias audio/vidéo", "*.mp4 *.mov *.m4a *.mp3 *.wav *.mkv *.avi *.flac *.aac"),
            ("Tous les fichiers", "*.*"),
        ]
        filename = filedialog.askopenfilename(
            title="Choisir un fichier audio ou vidéo",
            filetypes=filetypes,
        )
        root.destroy()
        return normalize_media_path(filename or None)
    except Exception as e:
        print(f"Sélection via GUI indisponible (Tk indisponible: {e})", file=sys.stderr)
        return normalize_media_path(prompt_cli())

# ---------- Main ----------
def main():
    parser = argparse.ArgumentParser(description="Transcrire un média audio/vidéo avec Whisper (Python).")
    parser.add_argument("video", nargs="?", help="Chemin du fichier audio/vidéo à transcrire")
    parser.add_argument("--model", default=os.getenv("MODEL", "medium"),
                        help="Modèle Whisper: tiny/base/small/medium/large (def=medium)")
    parser.add_argument("--lang", default=os.getenv("LANG", "auto"),
                        help="Langue forcée ex: fr, en, auto (def=auto)")
    parser.add_argument("--keep-audio", action="store_true", help="Ne pas supprimer le WAV temporaire")
    args = parser.parse_args()

    video_path = normalize_media_path(args.video)
    if not video_path:
        video_path = pick_media_file()
        if not video_path:
            print("Aucun fichier sélectionné, abandon.", file=sys.stderr)
            sys.exit(1)

    video = Path(video_path).expanduser().resolve()
    if not video.exists():
        print(f"ERREUR: fichier introuvable: {video}", file=sys.stderr)
        sys.exit(1)

    name = video.stem
    outdir = video.parent / f"SRT - {name}"
    outdir.mkdir(parents=True, exist_ok=True)
    log_path = outdir / f"{name}.log"

    # Logger vers fichier + stdout
    log = logging.getLogger("transcript")
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)

    log.info("===== Transcription Whisper (Python) =====")
    log.info("Date: %s", dt.datetime.now().isoformat(timespec="seconds"))
    log.info("Fichier: %s", video)
    log.info("Sortie: %s", outdir)
    log.info("Modèle: %s", args.model)
    log.info("Langue: %s", args.lang)

    # Checks outils
    if which("ffmpeg") is None:
        log.error("ffmpeg introuvable. Installe-le: brew install ffmpeg")
        sys.exit(2)
    log.debug("ffmpeg: %s", which("ffmpeg"))

    # Extraction audio normalisée (mono 16k, loudness)
    tmp_wav = outdir / f"{name}.temp.16k.wav"
    log.info("Extraction audio → %s", tmp_wav)
    cmd = [
        "ffmpeg", "-y", "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000",
        "-af", "loudnorm=I=-16:LRA=11:TP=-1.5",
        str(tmp_wav)
    ]
    run(cmd, log)

    # Espace disque
    try:
        st = os.statvfs(outdir)
        free_mb = (st.f_bavail * st.f_frsize) // (1024 * 1024)
        if free_mb < 2000:
            log.error("Espace disque insuffisant (%s Mo libres, besoin ~2000 Mo)", free_mb)
            sys.exit(3)
    except Exception as e:
        log.warning("Impossible de vérifier l’espace disque: %s", e)

    # Import whisper (lib)
    try:
        import whisper  # type: ignore
    except Exception as e:
        log.error("Module openai-whisper manquant. Installe : pip3 install -U openai-whisper (%s)", e)
        sys.exit(4)

    # Chargement modèle (GPU Apple Silicon pris en charge automatiquement)
    log.info("Chargement modèle %s…", args.model)
    model = whisper.load_model(args.model)

    # Options langue
    task_kwargs = {}
    if args.lang and args.lang != "auto":
        task_kwargs["language"] = args.lang

    # Transcription
    log.info("Transcription en cours…")
    result = model.transcribe(str(tmp_wav), **task_kwargs)
    segments = []
    for seg in result.get("segments", []):
        # chaque seg: {"id":..,"start":..,"end":..,"text":..}
        segments.append({
            "start": float(seg["start"]),
            "end": float(seg["end"]),
            "text": seg["text"],
        })
    full_text = result.get("text", "").strip()

    # Écriture fichiers
    txt_path = outdir / f"{name}.txt"
    srt_path = outdir / f"{name}.srt"
    vtt_path = outdir / f"{name}.vtt"

    write_txt(txt_path, segments if segments else [{"text": full_text, "start": 0.0, "end": 0.0}])
    write_srt(srt_path, segments) if segments else srt_path.write_text("", encoding="utf-8")
    write_vtt(vtt_path, segments) if segments else vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")

    log.info("Fichiers générés : %s, %s, %s", txt_path.name, srt_path.name, vtt_path.name)

    # Presse-papiers
    if full_text:
        pbcopy(full_text, log)
        log.info("Texte copié dans le presse-papiers.")

    # Nettoyage
    if not args.keep_audio:
        try:
            tmp_wav.unlink(missing_ok=True)
        except Exception as e:
            log.warning("Suppression WAV temporaire échouée: %s", e)

    # Ouvrir le dossier
    open_in_finder(outdir, log)
    log.info("OK: Fin normale.")

if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        print(f"ERREUR: commande échouée ({e})", file=sys.stderr)
        sys.exit(e.returncode or 10)
    except KeyboardInterrupt:
        print("Interrompu.", file=sys.stderr)
        sys.exit(130)
