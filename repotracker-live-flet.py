# -*- coding: utf-8 -*-
"""
RepoTracker — Control de versiones por reportes .txt
Flet 0.28.3  |  Desktop  |  Python 3.10+
"""

import sys

# UTF-8 global para evitar charmap en Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import flet as ft
import json, hashlib, difflib, threading, time
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ─────────────────────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────────────────────

VERSIONS_ROOT = Path.home() / ".repo_versions"
VERSIONS_ROOT.mkdir(exist_ok=True)

CFG_PATH = Path.home() / ".repo_tracker_config.json"

IGNORE_DIRS = {
    "__pycache__", ".git", "node_modules",
    "venv", ".venv", "dist", "build",
    ".idea", ".vscode", "test-de-codigo",
}
IGNORE_FILES = {".DS_Store", ".env"}
BIN_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".exe", ".dll", ".so", ".zip", ".tar", ".gz", ".7z",
    ".db", ".sqlite", ".pyc", ".pyd",
}

# Paleta terminal oscura
BG      = "#0D0F14"
SURFACE = "#141720"
BORDER  = "#252A38"
GREEN   = "#4FFFB0"
RED     = "#FF6B6B"
YELLOW  = "#FFD166"
BLUE    = "#74C0FC"
TEXT    = "#E8EAF0"
MUTED   = "#5A6075"
RAIL    = "#0A0C10"

# Caracteres árbol (Unicode escapeados para sobrevivir cualquier heredoc/editor)
T_MID  = "\u251c\u2500\u2500 "   # ├──
T_LAST = "\u2514\u2500\u2500 "   # └──
T_VERT = "\u2502   "             # │


# ─────────────────────────────────────────────────────────────
# CORE — sistema de archivos
# ─────────────────────────────────────────────────────────────

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    except Exception:
        return ""
    return h.hexdigest()


def read_safe(path: Path) -> str:
    for enc in ("utf-8", "latin-1", "cp1252", "iso-8859-1"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
        except Exception as e:
            return f"[ERROR: {e}]"
    return "[CONTENIDO NO DISPONIBLE]"


def collect_files(repo: Path, cfg: dict) -> list:
    ignore_d = set(cfg.get("ignore_dirs",  list(IGNORE_DIRS)))
    ignore_f = set(cfg.get("ignore_files", list(IGNORE_FILES)))
    inc_ext  = set(cfg.get("include_ext",  [])) or None
    exc_ext  = set(cfg.get("exclude_ext",  []))
    max_mb   = float(cfg.get("max_mb", 10) or 0)
    out = []
    for p in sorted(repo.rglob("*")):
        if not p.is_file():
            continue
        if any(part in ignore_d for part in p.parts):
            continue
        if p.name in ignore_f:
            continue
        ext = p.suffix.lower()
        if ext in BIN_EXT:
            continue
        if inc_ext and ext not in inc_ext:
            continue
        if ext in exc_ext:
            continue
        if max_mb:
            try:
                if p.stat().st_size / 1_048_576 > max_mb:
                    continue
            except Exception:
                pass
        out.append(p)
    return out


def build_tree(directory: Path, prefix: str = "") -> list:
    """Árbol visual completo — igual al script original, sin filtrar."""
    lines = []
    try:
        elems = sorted(
            [e for e in Path(directory).iterdir()
             if e.name not in IGNORE_DIRS and e.name not in IGNORE_FILES],
            key=lambda x: (not x.is_dir(), x.name.lower()),
        )
    except PermissionError:
        return []
    total = len(elems)
    for i, el in enumerate(elems):
        last = i == total - 1
        conn = T_LAST if last else T_MID
        ext  = "    " if last else T_VERT
        if el.is_dir():
            lines.append(prefix + conn + el.name + "/")
            lines.extend(build_tree(el, prefix + ext))
        else:
            lines.append(prefix + conn + el.name)
    return lines


def build_index(repo: Path, files: list) -> dict:
    idx = {}
    for f in files:
        rel = str(f.relative_to(repo))
        try:
            st = f.stat()
            idx[rel] = {
                "hash":    sha256(f),
                "mtime":   st.st_mtime,
                "size":    st.st_size,
                "content": read_safe(f),
            }
        except Exception:
            pass
    return idx


def make_report(repo: Path, files: list, tag: str,
                prev: dict, cfg: dict) -> str:
    SEP = "=" * 80
    lines = [
        SEP,
        f"REPORTE: {tag}",
        f"DIRECTORIO: {repo}",
        f"FECHA: {datetime.now().isoformat()}",
        SEP, "",
    ]

    # Árbol completo (sin filtros, igual al original)
    lines += [SEP, "ÁRBOL DEL REPOSITORIO", SEP, "", str(repo) + "/"]
    lines += build_tree(repo)
    lines.append("")

    rel_set = {str(f.relative_to(repo)) for f in files}

    if prev:
        # ── Iteración: resumen de cambios ────────────────────
        added    = [f for f in files if str(f.relative_to(repo)) not in prev]
        modified = [f for f in files
                    if str(f.relative_to(repo)) in prev
                    and sha256(f) != prev[str(f.relative_to(repo))]["hash"]]
        deleted  = [k for k in prev if k not in rel_set]

        lines += [SEP, "CAMBIOS RESPECTO A VERSIÓN ANTERIOR", SEP, ""]
        for f in added:    lines.append(f"  [+] {f.relative_to(repo)}")
        for f in modified: lines.append(f"  [~] {f.relative_to(repo)}")
        for k in deleted:  lines.append(f"  [-] {k}")
        lines.append("")

        # ── Diffs en sección propia (separada del snapshot) ──
        if modified:
            lines += [SEP, "DIFFS DE ARCHIVOS MODIFICADOS", SEP]
            for f in modified:
                rel  = str(f.relative_to(repo))
                old  = prev[rel].get("content", "")
                new  = read_safe(f)
                diff = list(difflib.unified_diff(
                    old.splitlines(),
                    new.splitlines(),
                    fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="",
                ))
                lines += ["", SEP, f"DIFF: {rel}", SEP, ""]
                if diff:
                    lines += diff
                else:
                    lines.append("(sin cambios en contenido de texto)")
                lines += ["", SEP, f"FIN DIFF: {rel}", SEP]

    else:
        # ── Inicial: métricas completas igual al original ────
        ext_c  = defaultdict(int)
        fold_c = defaultdict(int)
        for f in files:
            ext_c[f.suffix.lower() or "[sin_extension]"] += 1
            rp = str(f.relative_to(repo).parent)
            fold_c["/" if rp == "." else rp] += 1

        lines += [SEP, "MÉTRICAS", SEP, ""]
        lines.append("Archivos por extensión:")
        for ext, cnt in sorted(ext_c.items()):
            lines.append(f"  {ext}: {cnt}")

        lines.append("")
        lines.append("Carpetas y cantidad de archivos:")
        for folder, cnt in sorted(fold_c.items()):
            lines.append(f"  {folder}/: {cnt}")

        inc = cfg.get("include_ext", [])
        exc = cfg.get("exclude_ext", [])
        mb  = cfg.get("max_mb", 10)
        if inc or exc or mb:
            lines.append("")
            lines.append("Filtros aplicados:")
            if inc: lines.append(f"  - Solo incluir extensiones: {', '.join(inc)}")
            if exc: lines.append(f"  - Excluir extensiones: {', '.join(exc)}")
            if mb:  lines.append(f"  - Límite de tamaño: {mb}MB")

        lines.append("")

    # ── Snapshot completo de TODOS los archivos ───────────────
    # Igual al original tkinter: siempre el estado actual completo,
    # sin diff mezclado dentro del contenido.
    lines += [SEP, "SNAPSHOT DE ARCHIVOS", SEP]
    for f in files:
        rel     = str(f.relative_to(repo))
        content = read_safe(f)
        lines  += ["", SEP, f"INICIO ARCHIVO: {rel}", SEP, ""]
        lines.append(content)
        lines += ["", SEP, f"FIN ARCHIVO: {rel}", SEP]

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# CORE — versiones
# ─────────────────────────────────────────────────────────────

def _vdir(repo: Path) -> Path:
    key = repo.name + "_" + hashlib.md5(str(repo).encode()).hexdigest()[:8]
    d   = VERSIONS_ROOT / key
    d.mkdir(exist_ok=True)
    return d


def _meta(vdir: Path) -> dict:
    p = vdir / "meta.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"versions": []}


def _save_meta(vdir: Path, meta: dict):
    (vdir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def get_versions(repo: Path) -> list:
    return _meta(_vdir(repo))["versions"]


def get_index(repo: Path, tag: str) -> dict:
    vdir = _vdir(repo)
    for v in _meta(vdir)["versions"]:
        if v["tag"] == tag:
            p = vdir / v["index_file"]
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
    return {}


def next_tag(repo: Path) -> str:
    return f"v{len(get_versions(repo)) + 1}"


def save_version(repo: Path, tag: str, cfg: dict,
                 comment: str = "", on_progress=None) -> dict:
    vdir = _vdir(repo)
    meta = _meta(vdir)

    prev = None
    if meta["versions"]:
        p = vdir / meta["versions"][-1]["index_file"]
        if p.exists():
            prev = json.loads(p.read_text(encoding="utf-8"))

    def upd(msg, pct):
        if on_progress:
            on_progress(msg, pct)

    upd("Recopilando archivos...", 10)
    files = collect_files(repo, cfg)

    upd("Calculando hashes...", 30)
    idx = build_index(repo, files)

    upd("Generando reporte...", 55)
    txt = make_report(repo, files, tag, prev or {}, cfg)

    upd("Guardando...", 80)
    (vdir / f"{tag}.txt").write_text(txt, encoding="utf-8")
    (vdir / f"{tag}_index.json").write_text(
        json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")

    # Marcar todos los commits pre-reporte con este tag
    tag_commits_with_version(repo, tag)

    info = {
        "tag":         tag,
        "date":        datetime.now().isoformat(),
        "report_file": f"{tag}.txt",
        "index_file":  f"{tag}_index.json",
        "file_count":  len(files),
        "comment":     comment,
    }
    meta["versions"].append(info)
    _save_meta(vdir, meta)
    upd("Listo", 100)
    return info


def diff_vs_last(repo: Path, cfg: dict) -> dict:
    """Cambios respecto a la última versión guardada."""
    versions = get_versions(repo)
    if not versions:
        return {}
    last = get_index(repo, versions[-1]["tag"])
    if not last:
        return {}
    files   = collect_files(repo, cfg)
    current = {str(f.relative_to(repo)): sha256(f) for f in files}
    return {
        "added":    [k for k in current if k not in last],
        "modified": [k for k in current
                     if k in last and current[k] != last[k]["hash"]],
        "deleted":  [k for k in last if k not in current],
    }


def restore(repo: Path, tag: str, dest: Path):
    idx = get_index(repo, tag)
    dest.mkdir(parents=True, exist_ok=True)
    for rel, data in idx.items():
        t = dest / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(data.get("content", ""), encoding="utf-8")



# ─────────────────────────────────────────────────────────────
# FILE COMMITS — historial de cambios por archivo
# ─────────────────────────────────────────────────────────────

def _commits_path(repo: Path) -> Path:
    return _vdir(repo) / "file_commits.json"


def load_commits(repo: Path) -> dict:
    """Retorna {rel_path: [commit, ...]} desde disco."""
    p = _commits_path(repo)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_commits(repo: Path, commits: dict):
    _commits_path(repo).write_text(
        json.dumps(commits, indent=2, ensure_ascii=False),
        encoding="utf-8")


def push_file_commit(repo: Path, rel_path: str, content: str,
                     file_hash: str, report_tag: str = "") -> dict:
    """
    Agrega un commit para un archivo incluyendo diff contra el anterior.
    Retorna el commit creado, o {} si no hubo cambio real.
    """
    commits  = load_commits(repo)
    history  = commits.get(rel_path, [])

    # No duplicar si el hash no cambió desde el último commit
    if history and history[-1]["hash"] == file_hash:
        return {}

    # Calcular diff contra el commit anterior
    diff_lines = []
    if history:
        prev_content = history[-1].get("content", "")
        diff_lines = list(difflib.unified_diff(
            prev_content.splitlines(),
            content.splitlines(),
            lineterm="", n=3,
        ))

    commit = {
        "ts":         datetime.now().isoformat(),
        "hash":       file_hash,
        "content":    content,
        "diff":       diff_lines,     # vacío = primer snapshot
        "report":     report_tag,     # "" = pre-reporte, "v2" = vinculado a v2
    }
    history.append(commit)
    commits[rel_path] = history
    save_commits(repo, commits)
    return commit


def tag_commits_with_version(repo: Path, tag: str):
    """Marca todos los commits sin tag de la sesión actual con el tag del reporte."""
    commits = load_commits(repo)
    changed = False
    for rel_path in commits:
        for c in commits[rel_path]:
            if c.get("report", "") == "":
                c["report"] = tag
                changed = True
    if changed:
        save_commits(repo, commits)


def get_file_history(repo: Path, rel_path: str) -> list:
    """Devuelve commits de un archivo, del más antiguo al más reciente."""
    return load_commits(repo).get(rel_path, [])

# Alias para compatibilidad interna
get_file_commits = get_file_history

# ─────────────────────────────────────────────────────────────

class _Watcher(FileSystemEventHandler):
    def __init__(self, cb, on_file_change=None):
        """
        cb              — callback general (actualiza UI)
        on_file_change  — callback(path: str) llamado con la ruta absoluta
                          del archivo que cambió, para registrar commits
        """
        super().__init__()
        self._cb             = cb
        self._on_file_change = on_file_change
        self._pending        = {}
        self._lock           = threading.Lock()

    def _debounce(self, path):
        def fire():
            time.sleep(0.8)
            with self._lock:
                if self._pending.get(path) is threading.current_thread():
                    del self._pending[path]
                    if self._on_file_change:
                        self._on_file_change(path)
                    self._cb()
        t = threading.Thread(target=fire, daemon=True)
        with self._lock:
            self._pending[path] = t
        t.start()

    def on_any_event(self, ev):
        if ev.is_directory:
            return
        p = Path(ev.src_path)
        if any(part in IGNORE_DIRS for part in p.parts):
            return
        if p.suffix.lower() in BIN_EXT:
            return
        self._debounce(ev.src_path)



# ─────────────────────────────────────────────────────────────
# UI helpers
# ─────────────────────────────────────────────────────────────

def badge(text, color):
    return ft.Container(
        ft.Text(text, size=10, color=BG, weight=ft.FontWeight.BOLD),
        bgcolor=color,
        padding=ft.padding.symmetric(horizontal=6, vertical=2),
        border_radius=4,
    )


def label(text):
    return ft.Text(text, size=11, color=MUTED, weight=ft.FontWeight.W_600,
                   style=ft.TextStyle(letter_spacing=1.4))


def mono(text, size=12, color=TEXT, weight=None, expand=None, style=None, **kw):
    ts = ft.TextStyle(font_family="monospace")
    if style:
        ts.decoration       = getattr(style, "decoration", None)
        ts.decoration_color = getattr(style, "decoration_color", None)
    return ft.Text(text, size=size, color=color, weight=weight,
                   expand=expand, style=ts, **kw)


def divider():
    return ft.Divider(height=1, color=BORDER)


def single_tf(lbl, value, hint, on_change, width=None):
    kw = {"width": width} if width else {"expand": True}
    return ft.TextField(
        label=lbl, value=value, hint_text=hint,
        bgcolor=SURFACE, border_color=BORDER,
        focused_border_color=GREEN, color=TEXT,
        label_style=ft.TextStyle(color=MUTED),
        hint_style=ft.TextStyle(color=MUTED),
        text_size=12,
        text_style=ft.TextStyle(font_family="monospace"),
        on_change=on_change,
        **kw,
    )


def section_box(title, *controls):
    return ft.Container(
        ft.Column([
            ft.Text(title, size=11, color=MUTED, weight=ft.FontWeight.W_600),
            ft.Divider(height=1, color=BORDER),
            *controls,
        ], spacing=10),
        bgcolor=SURFACE,
        border=ft.border.all(1, BORDER),
        border_radius=8,
        padding=14,
    )


# ─────────────────────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────────────────────

class App:

    def __init__(self, page: ft.Page):
        self.page      = page
        self.repo: Path | None = None
        self.cfg       = self._load_cfg()
        self._observer = None
        self._nav_idx  = 0
        self._fp       = ft.FilePicker(on_result=self._on_dir_result)
        # Estado live: cambios sin guardar respecto a la última versión
        self._live_ch: dict = {}   # resultado de diff_vs_last en tiempo real
        self._setup()
        self._build()

    # ── Config persistente ────────────────────────────────────

    @staticmethod
    def _load_cfg() -> dict:
        defaults = {
            "ignore_dirs":  list(IGNORE_DIRS),
            "ignore_files": list(IGNORE_FILES),
            "include_ext":  [],
            "exclude_ext":  [],
            "max_mb":       10,
        }
        if CFG_PATH.exists():
            try:
                defaults.update(json.loads(CFG_PATH.read_text(encoding="utf-8")))
            except Exception:
                pass
        return defaults

    def _save_cfg(self):
        CFG_PATH.write_text(
            json.dumps(self.cfg, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Page setup ────────────────────────────────────────────

    def _setup(self):
        p = self.page
        p.title      = "RepoTracker"
        p.bgcolor    = BG
        p.padding    = 0
        p.theme_mode = ft.ThemeMode.DARK
        p.theme      = ft.Theme(color_scheme_seed=GREEN)
        p.window.width      = 1120
        p.window.height     = 730
        p.window.min_width  = 820
        p.window.min_height = 560
        p.overlay.append(self._fp)

    # ── Layout ────────────────────────────────────────────────

    def _build(self):
        self._views = [
            self._mk_files(),
            self._mk_history(),
            self._mk_config(),
            self._mk_timeline(),
        ]

        self._rail = ft.NavigationRail(
            bgcolor=RAIL,
            selected_index=0,
            min_width=64,
            min_extended_width=160,
            group_alignment=-1.0,
            label_type=ft.NavigationRailLabelType.ALL,
            indicator_color=ft.Colors.with_opacity(0.15, GREEN),
            destinations=[
                ft.NavigationRailDestination(
                    icon=ft.Icons.FOLDER_OUTLINED,
                    selected_icon=ft.Icons.FOLDER,
                    label="Archivos",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.HISTORY_OUTLINED,
                    selected_icon=ft.Icons.HISTORY,
                    label="Historial",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.TUNE_OUTLINED,
                    selected_icon=ft.Icons.TUNE,
                    label="Config",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.TIMELINE_OUTLINED,
                    selected_icon=ft.Icons.TIMELINE,
                    label="Timeline",
                ),
            ],
            on_change=self._on_nav,
        )

        self._dir_txt = ft.Text(
            "Sin directorio", size=12, color=MUTED,
            font_family="monospace",
            overflow=ft.TextOverflow.ELLIPSIS, max_lines=1, expand=True,
        )

        topbar = ft.Container(
            ft.Row([
                ft.Row([
                    ft.Icon(ft.Icons.TERMINAL, color=GREEN, size=16),
                    ft.Text("RepoTracker", size=13, color=TEXT,
                            weight=ft.FontWeight.W_600),
                ], spacing=8),
                self._dir_txt,
                ft.TextButton(
                    "Abrir directorio",
                    icon=ft.Icons.FOLDER_OPEN_OUTLINED,
                    style=ft.ButtonStyle(color=GREEN),
                    on_click=lambda _: self._fp.get_directory_path(
                        dialog_title="Seleccionar directorio"),
                ),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            bgcolor=RAIL,
            padding=ft.padding.symmetric(horizontal=20, vertical=10),
            border=ft.border.only(bottom=ft.BorderSide(1, BORDER)),
        )

        self._content = ft.Container(
            content=self._views[0], expand=True, padding=20)

        self.page.add(ft.Column([
            topbar,
            ft.Row([
                self._rail,
                ft.VerticalDivider(width=1, color=BORDER),
                self._content,
            ], expand=True, spacing=0),
        ], spacing=0, expand=True))

    # ── VIEW: ARCHIVOS ────────────────────────────────────────

    def _mk_files(self):
        self._banner = ft.Container(visible=False)
        self._tree   = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, expand=True)
        self._empty  = ft.Container(
            ft.Column([
                ft.Icon(ft.Icons.FOLDER_OPEN_OUTLINED, size=52, color=MUTED),
                ft.Text("Abrí un directorio para comenzar",
                        size=14, color=MUTED, text_align=ft.TextAlign.CENTER),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12),
            alignment=ft.alignment.center, expand=True,
        )
        return ft.Column([
            label("ARCHIVOS"), divider(),
            self._banner, self._empty, self._tree,
        ], spacing=8, expand=True)

    def _render_files(self):
        if not self.repo:
            return
        self._empty.visible = False
        ch = diff_vs_last(self.repo, self.cfg)
        self._live_ch = ch
        self._render_banner(ch)
        self._render_tree(ch)
        if self._nav_idx == 1:
            self._refresh_live_card()
        elif self._nav_idx == 3:
            self._render_timeline()
        self.page.update()

    def _render_tree(self, ch: dict):
        self._tree.controls.clear()
        mod = set(ch.get("modified", []))
        add = set(ch.get("added",    []))
        dlt = set(ch.get("deleted",  []))

        groups: dict[str, list] = defaultdict(list)
        for f in collect_files(self.repo, self.cfg):
            folder = str(f.relative_to(self.repo).parent)
            groups["/" if folder == "." else folder].append(f)

        for folder in sorted(groups):
            self._tree.controls.append(ft.Container(
                ft.Row([
                    ft.Icon(ft.Icons.FOLDER_OUTLINED, size=13, color=MUTED),
                    mono(folder, size=11, color=MUTED),
                ], spacing=6),
                padding=ft.padding.only(top=10, bottom=2),
            ))
            for f in sorted(groups[folder], key=lambda x: x.name):
                rel = str(f.relative_to(self.repo))
                if rel in mod:
                    b, bg = badge("~", YELLOW), ft.Colors.with_opacity(0.04, YELLOW)
                elif rel in add:
                    b, bg = badge("+", BLUE),   ft.Colors.with_opacity(0.04, BLUE)
                else:
                    b, bg = None, ft.Colors.TRANSPARENT

                row = ft.Row([
                    ft.Icon(ft.Icons.INSERT_DRIVE_FILE_OUTLINED, size=13, color=MUTED),
                    mono(f.name, expand=True),
                    *(([b]) if b else []),
                ], spacing=8)
                self._tree.controls.append(ft.Container(
                    row, bgcolor=bg, border_radius=4,
                    padding=ft.padding.symmetric(horizontal=8, vertical=4),
                ))

        for rel in sorted(dlt):
            self._tree.controls.append(ft.Container(
                ft.Row([
                    ft.Icon(ft.Icons.REMOVE_CIRCLE_OUTLINE, size=13, color=RED),
                    mono(rel, color=RED,
                         style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH)),
                    badge("-", RED),
                ], spacing=8),
                bgcolor=ft.Colors.with_opacity(0.04, RED), border_radius=4,
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
            ))

    def _render_banner(self, ch: dict):
        versions = get_versions(self.repo)
        n = sum(len(ch.get(k, [])) for k in ("added", "modified", "deleted"))

        if not versions:
            self._banner.visible = True
            self._banner.content = self._banner_initial(next_tag(self.repo))
        elif n > 0:
            self._banner.visible = True
            self._banner.content = self._banner_changes(
                versions[-1]["tag"], next_tag(self.repo), ch)
        else:
            self._banner.visible = False

    def _banner_initial(self, tag_hint):
        tf = ft.TextField(
            value=tag_hint, width=110, height=38, text_size=12,
            bgcolor=BG, border_color=GREEN, focused_border_color=GREEN,
            color=GREEN, text_style=ft.TextStyle(font_family="monospace"),
            content_padding=ft.padding.symmetric(horizontal=10, vertical=6),
        )
        return ft.Container(
            ft.Row([
                ft.Icon(ft.Icons.INFO_OUTLINE, color=GREEN, size=16),
                ft.Text("Sin versiones — generá el reporte inicial:",
                        size=12, color=TEXT),
                tf,
                ft.ElevatedButton(
                    "Generar reporte inicial",
                    icon=ft.Icons.SAVE_OUTLINED,
                    style=ft.ButtonStyle(bgcolor=GREEN, color=BG,
                        shape=ft.RoundedRectangleBorder(radius=6)),
                    on_click=lambda _: self._do_save(tf.value),
                ),
            ], spacing=12, wrap=True),
            bgcolor=ft.Colors.with_opacity(0.08, GREEN),
            border=ft.border.all(1, ft.Colors.with_opacity(0.3, GREEN)),
            border_radius=8, padding=12,
        )

    def _banner_changes(self, last_tag, new_tag, ch):
        mod, add, dlt = (len(ch.get(k, [])) for k in ("modified", "added", "deleted"))
        parts = []
        if mod: parts.append(f"{mod} modificado{'s' if mod > 1 else ''}")
        if add: parts.append(f"{add} nuevo{'s' if add > 1 else ''}")
        if dlt: parts.append(f"{dlt} eliminado{'s' if dlt > 1 else ''}")

        tf = ft.TextField(
            value=new_tag, width=90, height=38, text_size=12,
            bgcolor=BG, border_color=YELLOW, focused_border_color=YELLOW,
            color=YELLOW, text_style=ft.TextStyle(font_family="monospace"),
            content_padding=ft.padding.symmetric(horizontal=10, vertical=6),
        )
        return ft.Container(
            ft.Row([
                ft.Icon(ft.Icons.CHANGE_CIRCLE_OUTLINED, color=YELLOW, size=16),
                ft.Text(f"Cambios desde {last_tag}: {', '.join(parts)}",
                        size=12, color=TEXT),
                tf,
                ft.ElevatedButton(
                    "Generar reporte de iteración",
                    icon=ft.Icons.SAVE_OUTLINED,
                    style=ft.ButtonStyle(bgcolor=YELLOW, color=BG,
                        shape=ft.RoundedRectangleBorder(radius=6)),
                    on_click=lambda _: self._do_save(tf.value),
                ),
            ], spacing=12, wrap=True),
            bgcolor=ft.Colors.with_opacity(0.08, YELLOW),
            border=ft.border.all(1, ft.Colors.with_opacity(0.3, YELLOW)),
            border_radius=8, padding=12,
        )


    # ── VIEW: HISTORIAL + TIMELINE + DIFF ────────────────────

    LIVE_TAG = "__live__"   # tag virtual del pre-reporte

    def _mk_history(self):
        self._tl_selected_tag  = None
        self._tl_selected_file = None

        self._tl_list = ft.Column(
            spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)

        self._tl_files_title = ft.Text(
            "Seleccioná una versión", size=12, color=MUTED)
        self._tl_files_col = ft.Column(
            spacing=2, scroll=ft.ScrollMode.AUTO, expand=True)

        self._tl_diff_title = ft.Text(
            "", size=11, color=MUTED,
            style=ft.TextStyle(letter_spacing=0.5))
        self._tl_diff_col = ft.Column(
            spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)

        self._tl_empty = ft.Container(
            ft.Column([
                ft.Icon(ft.Icons.HISTORY_OUTLINED, size=52, color=MUTED),
                ft.Text("Aún no hay versiones guardadas",
                        size=14, color=MUTED,
                        text_align=ft.TextAlign.CENTER),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12),
            alignment=ft.alignment.center, expand=True,
        )

        panel_versions = ft.Container(
            ft.Column([
                ft.Text("VERSIONES", size=10, color=MUTED,
                        weight=ft.FontWeight.W_600,
                        style=ft.TextStyle(letter_spacing=1.2)),
                ft.Divider(height=1, color=BORDER),
                self._tl_list,
            ], spacing=6, expand=True),
            width=170,
            bgcolor=RAIL,
            border=ft.border.only(right=ft.BorderSide(1, BORDER)),
            padding=ft.padding.only(left=12, right=0, top=14, bottom=14),
        )

        panel_files = ft.Container(
            ft.Column([
                self._tl_files_title,
                ft.Divider(height=1, color=BORDER),
                self._tl_files_col,
            ], spacing=6, expand=True),
            width=210,
            border=ft.border.only(right=ft.BorderSide(1, BORDER)),
            padding=ft.padding.symmetric(horizontal=12, vertical=14),
        )

        panel_diff = ft.Container(
            ft.Column([
                self._tl_diff_title,
                ft.Divider(height=1, color=BORDER),
                self._tl_diff_col,
            ], spacing=6, expand=True),
            expand=True,
            padding=ft.padding.symmetric(horizontal=14, vertical=14),
        )

        return ft.Column([
            ft.Row([
                label("HISTORIAL"),
                ft.Row([
                    ft.TextButton(
                        "Ver .txt",
                        icon=ft.Icons.DESCRIPTION_OUTLINED,
                        style=ft.ButtonStyle(color=MUTED),
                        on_click=lambda _: self._show_report(
                            self._tl_selected_tag)
                        if self._tl_selected_tag
                        and self._tl_selected_tag != self.LIVE_TAG else None,
                    ),
                    ft.TextButton(
                        "Restaurar",
                        icon=ft.Icons.RESTORE_OUTLINED,
                        style=ft.ButtonStyle(color=BLUE),
                        on_click=lambda _: self._show_restore(
                            self._tl_selected_tag)
                        if self._tl_selected_tag
                        and self._tl_selected_tag != self.LIVE_TAG else None,
                    ),
                ], spacing=0),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            divider(),
            self._tl_empty,
            ft.Container(
                content=ft.Row([panel_versions, panel_files, panel_diff],
                               spacing=0, expand=True),
                expand=True, visible=True),
        ], spacing=8, expand=True)

    def _render_history(self):
        if not self.repo:
            return
        versions     = get_versions(self.repo)
        has_versions = bool(versions)
        self._tl_empty.visible = not has_versions

        self._tl_list.controls.clear()
        self._tl_files_col.controls.clear()
        self._tl_diff_col.controls.clear()

        if not has_versions:
            self.page.update()
            return

        tags = {v["tag"] for v in versions}
        if self._tl_selected_tag not in tags \
                and self._tl_selected_tag != self.LIVE_TAG:
            self._tl_selected_tag  = None
            self._tl_selected_file = None

        # Tarjeta live siempre al tope
        n_live = sum(len(self._live_ch.get(k, []))
                     for k in ("added", "modified", "deleted"))
        self._tl_list.controls.append(self._mk_live_card(n_live))

        for v in reversed(versions):
            self._tl_list.controls.append(self._mk_version_card(v))

        # Selección inicial
        if self._tl_selected_tag == self.LIVE_TAG:
            self._select_live(redraw_list=False)
        elif self._tl_selected_tag:
            self._select_version(self._tl_selected_tag, redraw_list=False)
        elif n_live > 0:
            self._tl_selected_tag = self.LIVE_TAG
            self._select_live(redraw_list=False)
        else:
            self._select_version(versions[-1]["tag"], redraw_list=False)

        self.page.update()

    # ── Tarjeta live ──────────────────────────────────────────

    def _mk_live_card(self, n_changes: int) -> ft.Container:
        versions   = get_versions(self.repo)
        next_n     = len(versions) + 1
        live_label = f"pre-v{next_n}"
        is_sel     = self._tl_selected_tag == self.LIVE_TAG
        has_ch     = n_changes > 0

        bg       = ft.Colors.with_opacity(0.12, YELLOW) if is_sel \
                   else ft.Colors.with_opacity(0.04, YELLOW) if has_ch \
                   else ft.Colors.TRANSPARENT
        border_c = YELLOW if is_sel \
                   else ft.Colors.with_opacity(0.3, YELLOW) if has_ch \
                   else BORDER
        lbl_col  = YELLOW if (is_sel or has_ch) else MUTED

        summary_row = ft.Row(spacing=4, wrap=True)
        ch = self._live_ch
        if ch.get("added"):
            summary_row.controls.append(badge(f"+{len(ch['added'])}", GREEN))
        if ch.get("modified"):
            summary_row.controls.append(badge(f"~{len(ch['modified'])}", YELLOW))
        if ch.get("deleted"):
            summary_row.controls.append(badge(f"-{len(ch['deleted'])}", RED))
        if not has_ch:
            summary_row.controls.append(
                ft.Text("sin cambios", size=9, color=MUTED))

        return ft.Container(
            ft.Column([
                ft.Row([
                    ft.Container(
                        width=8, height=8,
                        bgcolor=YELLOW if has_ch else MUTED,
                        border_radius=4,
                    ),
                    mono(live_label, size=13, color=lbl_col,
                         weight=ft.FontWeight.BOLD if is_sel
                         else ft.FontWeight.NORMAL),
                    ft.Container(
                        ft.Text("EN VIVO", size=8, color=BG,
                                weight=ft.FontWeight.BOLD),
                        bgcolor=YELLOW if has_ch else MUTED,
                        padding=ft.padding.symmetric(horizontal=4, vertical=1),
                        border_radius=3,
                    ),
                ], spacing=5),
                ft.Text("cambios sin guardar", size=9, color=MUTED),
                summary_row,
            ], spacing=3),
            bgcolor=bg,
            border=ft.border.all(1, border_c),
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=10, vertical=8),
            margin=ft.margin.only(right=12, bottom=6),
            ink=True,
            on_click=lambda _: self._select_live(),
        )

    def _refresh_live_card(self):
        """Actualiza solo la tarjeta live sin reconstruir toda la lista."""
        if not self._tl_list.controls:
            return
        n_live = sum(len(self._live_ch.get(k, []))
                     for k in ("added", "modified", "deleted"))
        self._tl_list.controls[0] = self._mk_live_card(n_live)
        if self._tl_selected_tag == self.LIVE_TAG:
            self._select_live(redraw_list=False)

    def _select_live(self, redraw_list: bool = True):
        """Selecciona el pre-reporte live y muestra los cambios actuales."""
        self._tl_selected_tag  = self.LIVE_TAG
        self._tl_selected_file = None

        if redraw_list:
            versions = get_versions(self.repo)
            n_live   = sum(len(self._live_ch.get(k, []))
                           for k in ("added", "modified", "deleted"))
            self._tl_list.controls.clear()
            self._tl_list.controls.append(self._mk_live_card(n_live))
            for v in reversed(versions):
                self._tl_list.controls.append(self._mk_version_card(v))

        last_versions = get_versions(self.repo)
        last_tag      = last_versions[-1]["tag"] if last_versions else None
        prev_idx      = get_index(self.repo, last_tag) if last_tag else {}

        # Índice en tiempo real desde disco
        files    = collect_files(self.repo, self.cfg)
        curr_idx = build_index(self.repo, files)

        next_n   = len(last_versions) + 1
        now_str  = datetime.now().strftime("%d/%m/%Y %H:%M")
        self._tl_files_title.value = f"pre-v{next_n}  ·  {now_str}"
        self._tl_files_title.color = YELLOW

        self._tl_files_col.controls.clear()
        ch = self._live_ch

        file_entries = (
            [(k, "added")    for k in ch.get("added",    [])] +
            [(k, "modified") for k in ch.get("modified", [])] +
            [(k, "deleted")  for k in ch.get("deleted",  [])]
        )

        if not file_entries:
            self._tl_files_col.controls.append(
                ft.Column([
                    ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE,
                            size=24, color=GREEN),
                    ft.Text("Sin cambios desde la última versión.",
                            size=11, color=MUTED,
                            text_align=ft.TextAlign.CENTER),
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                   spacing=6))
        else:
            for fname, ftype in sorted(
                    file_entries,
                    key=lambda x: (x[1] != "modified",
                                   x[1] != "added", x[0])):
                self._tl_files_col.controls.append(
                    self._mk_file_chip(
                        fname, ftype, self.LIVE_TAG, curr_idx, prev_idx))

        self._tl_diff_title.value = "Seleccioná un archivo"
        self._tl_diff_col.controls.clear()
        self.page.update()

    # ── Timeline: tarjeta de versión ──────────────────────────

    def _mk_version_card(self, v: dict) -> ft.Container:
        tag      = v["tag"]
        date_str = datetime.fromisoformat(v["date"]).strftime("%d/%m\n%H:%M")
        cnt      = v.get("file_count", 0)

        # Calcular cambios de esta versión vs la anterior
        change_info = self._version_change_summary(tag)
        is_selected = tag == self._tl_selected_tag

        dot_color = GREEN if not change_info else YELLOW
        bg        = ft.Colors.with_opacity(0.12, GREEN) if is_selected \
                    else ft.Colors.TRANSPARENT
        border_c  = GREEN if is_selected else ft.Colors.TRANSPARENT

        summary_row = ft.Row(spacing=4, wrap=True)
        if change_info.get("added"):
            summary_row.controls.append(
                badge(f"+{change_info['added']}", GREEN))
        if change_info.get("modified"):
            summary_row.controls.append(
                badge(f"~{change_info['modified']}", YELLOW))
        if change_info.get("deleted"):
            summary_row.controls.append(
                badge(f"-{change_info['deleted']}", RED))

        return ft.Container(
            ft.Column([
                ft.Row([
                    ft.Container(
                        width=8, height=8,
                        bgcolor=dot_color,
                        border_radius=4,
                    ),
                    mono(tag, size=13, color=GREEN if is_selected else TEXT,
                         weight=ft.FontWeight.BOLD if is_selected
                         else ft.FontWeight.NORMAL),
                ], spacing=6),
                ft.Text(date_str, size=10, color=MUTED),
                summary_row,
            ], spacing=3),
            bgcolor=bg,
            border=ft.border.all(1, border_c),
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=10, vertical=8),
            margin=ft.margin.only(right=12, bottom=4),
            ink=True,
            on_click=lambda _, t=tag: self._select_version(t),
        )

    def _version_change_summary(self, tag: str) -> dict:
        """Cambios de esta versión respecto a la anterior."""
        versions = get_versions(self.repo)
        tags     = [v["tag"] for v in versions]
        if tag not in tags:
            return {}
        i = tags.index(tag)
        if i == 0:
            # Primera versión: todos los archivos son "added"
            idx = get_index(self.repo, tag)
            return {"added": len(idx), "modified": 0, "deleted": 0}

        prev_tag  = tags[i - 1]
        curr_idx  = get_index(self.repo, tag)
        prev_idx  = get_index(self.repo, prev_tag)
        added    = sum(1 for k in curr_idx if k not in prev_idx)
        deleted  = sum(1 for k in prev_idx if k not in curr_idx)
        modified = sum(1 for k in curr_idx
                       if k in prev_idx
                       and curr_idx[k]["hash"] != prev_idx[k]["hash"])
        return {"added": added, "modified": modified, "deleted": deleted}

    # ── Timeline: selección de versión ────────────────────────

    def _select_version(self, tag: str, redraw_list: bool = True):
        self._tl_selected_tag  = tag
        self._tl_selected_file = None

        versions = get_versions(self.repo)
        tags     = [v["tag"] for v in versions]

        # Reconstruir lista con nueva selección resaltada
        if redraw_list:
            self._tl_list.controls.clear()
            for v in reversed(versions):
                self._tl_list.controls.append(self._mk_version_card(v))

        # Determinar archivos relevantes para esta versión
        curr_idx = get_index(self.repo, tag)
        i        = tags.index(tag)

        if i == 0:
            prev_idx = {}
        else:
            prev_idx = get_index(self.repo, tags[i - 1])

        # Clasificar archivos: added / modified / deleted / unchanged
        file_entries = []
        for k in curr_idx:
            if k not in prev_idx:
                file_entries.append((k, "added"))
            elif curr_idx[k]["hash"] != prev_idx[k]["hash"]:
                file_entries.append((k, "modified"))
        for k in prev_idx:
            if k not in curr_idx:
                file_entries.append((k, "deleted"))
        # Si primera versión, mostrar todos como nuevos
        if not file_entries and not prev_idx:
            for k in curr_idx:
                file_entries.append((k, "added"))

        v_info = next(v for v in versions if v["tag"] == tag)
        date   = datetime.fromisoformat(v_info["date"]).strftime("%d/%m/%Y %H:%M")

        self._tl_files_title.value   = f"{tag}  ·  {date}"
        self._tl_files_title.color   = GREEN
        self._tl_files_col.controls.clear()

        if not file_entries:
            self._tl_files_col.controls.append(
                ft.Text("Sin cambios respecto a la versión anterior.",
                        size=11, color=MUTED))
        else:
            for fname, ftype in sorted(file_entries,
                                       key=lambda x: (x[1] != "modified",
                                                       x[1] != "added", x[0])):
                self._tl_files_col.controls.append(
                    self._mk_file_chip(fname, ftype, tag, curr_idx, prev_idx))

        # Limpiar panel diff
        self._tl_diff_title.value = "Seleccioná un archivo"
        self._tl_diff_col.controls.clear()

        self.page.update()

    # ── Timeline: chip de archivo ─────────────────────────────

    def _mk_file_chip(self, fname: str, ftype: str,
                      tag: str, curr_idx: dict, prev_idx: dict) -> ft.Container:
        color_map = {"added": BLUE, "modified": YELLOW, "deleted": RED}
        sym_map   = {"added": "+", "modified": "~", "deleted": "-"}
        color     = color_map[ftype]
        is_sel    = fname == self._tl_selected_file

        bg       = ft.Colors.with_opacity(0.10, color) if is_sel \
                   else ft.Colors.with_opacity(0.03, color)
        border_c = color if is_sel else ft.Colors.TRANSPARENT

        short = fname.split("/")[-1] if "/" in fname else fname
        parent = "/".join(fname.split("/")[:-1]) if "/" in fname else ""

        return ft.Container(
            ft.Row([
                badge(sym_map[ftype], color),
                ft.Column([
                    mono(short, size=11,
                         color=color if is_sel else TEXT),
                    *([ ft.Text(parent, size=9, color=MUTED) ] if parent else []),
                ], spacing=1, tight=True),
            ], spacing=6),
            bgcolor=bg,
            border=ft.border.all(1, border_c),
            border_radius=5,
            padding=ft.padding.symmetric(horizontal=8, vertical=5),
            margin=ft.margin.only(bottom=3),
            ink=True,
            on_click=lambda _, f=fname, t=ftype, ci=curr_idx, pi=prev_idx:
                self._select_file(f, t, tag, ci, pi),
        )

    # ── Timeline: selección de archivo + render diff ──────────

    def _select_file(self, fname: str, ftype: str, tag: str,
                     curr_idx: dict, prev_idx: dict):
        self._tl_selected_file = fname

        # Refrescar chips con nueva selección resaltada
        if tag == self.LIVE_TAG:
            # Modo live: reconstruir desde _live_ch
            ch = self._live_ch
            file_entries = (
                [(k, "added")    for k in ch.get("added",    [])] +
                [(k, "modified") for k in ch.get("modified", [])] +
                [(k, "deleted")  for k in ch.get("deleted",  [])]
            )
        else:
            versions      = get_versions(self.repo)
            tags          = [v["tag"] for v in versions]
            i             = tags.index(tag)
            prev_idx_full = {} if i == 0 else get_index(self.repo, tags[i - 1])
            curr_idx_full = curr_idx
            file_entries  = []
            for k in curr_idx_full:
                if k not in prev_idx_full:
                    file_entries.append((k, "added"))
                elif curr_idx_full[k]["hash"] != prev_idx_full[k]["hash"]:
                    file_entries.append((k, "modified"))
            for k in prev_idx_full:
                if k not in curr_idx_full:
                    file_entries.append((k, "deleted"))
            if not file_entries and not prev_idx_full:
                for k in curr_idx_full:
                    file_entries.append((k, "added"))

        self._tl_files_col.controls.clear()
        for fn, ft_ in sorted(file_entries,
                               key=lambda x: (x[1] != "modified",
                                               x[1] != "added", x[0])):
            self._tl_files_col.controls.append(
                self._mk_file_chip(fn, ft_, tag, curr_idx, prev_idx))

        # Render diff
        self._tl_diff_title.value = fname
        self._render_diff(fname, ftype, curr_idx, prev_idx)
        self.page.update()

    def _render_diff(self, fname: str, ftype: str,
                     curr_idx: dict, prev_idx: dict):
        self._tl_diff_col.controls.clear()

        old_content = prev_idx.get(fname, {}).get("content", "") if ftype != "added"   else ""
        new_content = curr_idx.get(fname, {}).get("content", "") if ftype != "deleted" else ""

        if ftype == "added":
            self._tl_diff_col.controls.append(
                self._diff_info_row(ft.Icons.ADD_CIRCLE_OUTLINE, "Archivo nuevo", BLUE))
            self._render_diff_lines([], new_content.splitlines(), "added")
            return

        if ftype == "deleted":
            self._tl_diff_col.controls.append(
                self._diff_info_row(ft.Icons.REMOVE_CIRCLE_OUTLINE,
                                    "Archivo eliminado", RED))
            self._render_diff_lines(old_content.splitlines(), [], "deleted")
            return

        # Modificado: diff unidiff → renderizado visual línea por línea
        old_lines = old_content.splitlines()
        new_lines = new_content.splitlines()
        diff      = list(difflib.unified_diff(
            old_lines, new_lines,
            lineterm="", n=3,
        ))

        if not diff:
            self._tl_diff_col.controls.append(
                self._diff_info_row(ft.Icons.CHECK_CIRCLE_OUTLINE,
                                    "Contenido idéntico (solo metadatos cambiaron)", MUTED))
            return

        stats = self._diff_stats(diff)
        self._tl_diff_col.controls.append(
            ft.Container(
                ft.Row([
                    ft.Text(f"+{stats['added']}",
                            size=11, color=GREEN, weight=ft.FontWeight.BOLD,
                            style=ft.TextStyle(font_family="monospace")),
                    ft.Text(f"-{stats['removed']}",
                            size=11, color=RED, weight=ft.FontWeight.BOLD,
                            style=ft.TextStyle(font_family="monospace")),
                    ft.Text(f"  {fname.split('.')[-1] if '.' in fname else 'txt'}",
                            size=10, color=MUTED),
                ], spacing=8),
                padding=ft.padding.only(bottom=6),
            )
        )
        self._render_unified_diff(diff)

    def _diff_info_row(self, icon, text, color) -> ft.Container:
        return ft.Container(
            ft.Row([
                ft.Icon(icon, size=14, color=color),
                ft.Text(text, size=11, color=color),
            ], spacing=6),
            padding=ft.padding.only(bottom=8),
        )

    @staticmethod
    def _diff_stats(diff_lines: list) -> dict:
        added = removed = 0
        for l in diff_lines:
            if l.startswith("+") and not l.startswith("+++"):
                added += 1
            elif l.startswith("-") and not l.startswith("---"):
                removed += 1
        return {"added": added, "removed": removed}

    def _render_unified_diff(self, diff_lines: list, target=None):
        """
        Renderiza diff unidiff con UI optimizada:
        - Columna de números de línea fija y alineada
        - Símbolo +/- en columna propia
        - Contenido limpio (sin \\r\\n residual)
        - Hunks @@ como separadores visuales
        - target: Column destino. None = usa self._tl_diff_col
        """
        import re
        col          = target if target is not None else self._tl_diff_col
        line_old     = 0
        line_new     = 0

        for raw in diff_lines:
            # Limpiar cualquier \r residual de Windows
            line = raw.rstrip("\r\n")

            # ── Hunk header @@ ───────────────────────────────
            if line.startswith("@@"):
                m = re.search(r"-(\d+)(?:,\d+)? \+(\d+)(?:,\d+)?", line)
                if m:
                    line_old = int(m.group(1))
                    line_new = int(m.group(2))
                # Extraer texto extra del hunk (ej: nombre de función)
                hunk_extra = re.sub(r"^@@[^@]+@@\s*", "", line).strip()
                hunk_label = hunk_extra if hunk_extra else line
                col.controls.append(ft.Container(
                    ft.Row([
                        ft.Container(width=36),   # alinear con números
                        ft.Container(width=14),   # alinear con símbolo
                        ft.Text(hunk_label, size=10, color=MUTED,
                                style=ft.TextStyle(font_family="monospace"),
                                expand=True),
                    ], spacing=0, tight=True),
                    bgcolor=ft.Colors.with_opacity(0.06, MUTED),
                    padding=ft.padding.symmetric(horizontal=4, vertical=3),
                    margin=ft.margin.symmetric(vertical=2),
                    border_radius=3,
                ))
                continue

            # Saltar headers --- / +++
            if line.startswith("---") or line.startswith("+++"):
                continue

            # ── Líneas de contenido ───────────────────────────
            if line.startswith("+"):
                sym     = "+"
                content = line[1:]
                bg      = ft.Colors.with_opacity(0.13, GREEN)
                sym_col = GREEN
                txt_col = GREEN
                num_str = str(line_new)
                line_new += 1

            elif line.startswith("-"):
                sym     = "-"
                content = line[1:]
                bg      = ft.Colors.with_opacity(0.11, RED)
                sym_col = RED
                txt_col = RED
                num_str = str(line_old)
                line_old += 1

            else:
                # Línea de contexto: el primer char es siempre " "
                sym     = ""
                content = line[1:] if line else ""
                bg      = ft.Colors.TRANSPARENT
                sym_col = MUTED
                txt_col = ft.Colors.with_opacity(0.6, TEXT)
                num_str = str(line_new)
                line_old += 1
                line_new += 1

            col.controls.append(ft.Container(
                ft.Row([
                    # Número de línea — columna fija, alineado a la derecha
                    ft.Container(
                        ft.Text(num_str, size=10,
                                color=ft.Colors.with_opacity(0.4, sym_col)
                                      if sym else MUTED,
                                style=ft.TextStyle(font_family="monospace")),
                        width=36,
                        alignment=ft.alignment.center_right,
                        padding=ft.padding.only(right=4),
                    ),
                    # Símbolo +/- — columna fija
                    ft.Container(
                        ft.Text(sym, size=11,
                                color=sym_col,
                                weight=ft.FontWeight.BOLD,
                                style=ft.TextStyle(font_family="monospace")),
                        width=14,
                        alignment=ft.alignment.center,
                    ),
                    # Contenido de la línea
                    ft.Text(
                        content,
                        size=11,
                        color=txt_col,
                        style=ft.TextStyle(font_family="monospace"),
                        selectable=True,
                        expand=True,
                        no_wrap=True,
                        overflow=ft.TextOverflow.CLIP,
                    ),
                ], spacing=0, tight=True),
                bgcolor=bg,
                padding=ft.padding.symmetric(horizontal=4, vertical=1),
            ))

    def _render_diff_lines(self, old_lines: list,
                           new_lines: list, mode: str, target=None):
        """Para archivos nuevos/eliminados: renderiza todo el contenido.
        Si target es None usa self._tl_diff_col (historial de versiones)."""
        col    = target if target is not None else self._tl_diff_col
        lines  = new_lines if mode == "added" else old_lines
        color  = BLUE if mode == "added" else RED
        sym    = "+" if mode == "added" else "-"
        bg     = ft.Colors.with_opacity(0.08, color)

        MAX_LINES = 300
        for i, line in enumerate(lines[:MAX_LINES], 1):
            clean = line.rstrip("\r\n")
            col.controls.append(ft.Container(
                ft.Row([
                    ft.Container(
                        ft.Text(str(i), size=10,
                                color=ft.Colors.with_opacity(0.4, color),
                                style=ft.TextStyle(font_family="monospace")),
                        width=36, alignment=ft.alignment.center_right,
                        padding=ft.padding.only(right=4),
                    ),
                    ft.Container(
                        ft.Text(sym, size=11, color=color,
                                weight=ft.FontWeight.BOLD,
                                style=ft.TextStyle(font_family="monospace")),
                        width=14, alignment=ft.alignment.center,
                    ),
                    ft.Text(clean, size=11, color=color,
                            style=ft.TextStyle(font_family="monospace"),
                            selectable=True, expand=True,
                            no_wrap=True,
                            overflow=ft.TextOverflow.CLIP),
                ], spacing=0, tight=True),
                bgcolor=bg,
                padding=ft.padding.symmetric(horizontal=4, vertical=1),
            ))
        if len(lines) > MAX_LINES:
            col.controls.append(
                ft.Text(f"... {len(lines) - MAX_LINES} líneas más",
                        size=10, color=MUTED)
            )

    # ── VIEW: CONFIG ──────────────────────────────────────────

    def _mk_config(self):
        def csv_tf(lbl, key, hint, width=None):
            val = ", ".join(self.cfg.get(key, []))
            return single_tf(lbl, val, hint,
                             on_change=lambda e, k=key: self._cfg_csv(k, e.control.value),
                             width=width)

        self._c_inc    = csv_tf("Solo incluir extensiones (vacío = todas)",
                                "include_ext", "ej: .py, .js, .ts")
        self._c_exc    = csv_tf("Excluir extensiones adicionales",
                                "exclude_ext", "ej: .log, .tmp")
        self._c_idirs  = csv_tf("Carpetas ignoradas",
                                "ignore_dirs",  "ej: __pycache__, .git")
        self._c_ifiles = csv_tf("Archivos ignorados",
                                "ignore_files", "ej: .DS_Store, .env")
        self._c_mb = ft.TextField(
            label="Tamaño máximo por archivo (MB, 0 = sin límite)",
            value=str(self.cfg.get("max_mb", 10)),
            bgcolor=SURFACE, border_color=BORDER,
            focused_border_color=GREEN, color=TEXT,
            label_style=ft.TextStyle(color=MUTED),
            text_size=12, width=280,
            on_change=lambda e: self._cfg_mb(e.control.value),
        )

        return ft.Column([
            label("CONFIGURACIÓN DE FILTROS"), divider(),
            section_box("Extensiones",
                ft.Row([self._c_inc, self._c_exc], spacing=16)),
            section_box("Archivos y carpetas",
                self._c_idirs,
                self._c_ifiles),
            section_box("Tamaño de archivo",
                ft.Row([self._c_mb], spacing=0),
                ft.Text("0 = sin límite.  Los binarios siempre se excluyen.",
                        size=11, color=MUTED)),
            ft.Row([
                ft.ElevatedButton(
                    "Guardar configuración",
                    icon=ft.Icons.SAVE_OUTLINED,
                    style=ft.ButtonStyle(bgcolor=GREEN, color=BG,
                        shape=ft.RoundedRectangleBorder(radius=6)),
                    on_click=self._cfg_save,
                ),
                ft.ElevatedButton(
                    "Restaurar defaults",
                    icon=ft.Icons.RESTORE,
                    style=ft.ButtonStyle(color=TEXT, bgcolor=SURFACE,
                        shape=ft.RoundedRectangleBorder(radius=6),
                        side=ft.BorderSide(1, BORDER)),
                    on_click=self._cfg_reset,
                ),
            ], spacing=12),
            ft.Text("Los filtros se aplican en la siguiente generación de reporte.",
                    size=11, color=MUTED),
        ], spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)

    def _cfg_csv(self, key, value):
        self.cfg[key] = [v.strip() for v in value.split(",") if v.strip()]

    def _cfg_mb(self, value):
        try:
            self.cfg["max_mb"] = float(value)
        except ValueError:
            pass

    def _cfg_save(self, _):
        try:
            self._save_cfg()
            self._snack("Configuración guardada", GREEN)
        except Exception as ex:
            self._snack(f"Error al guardar: {ex}", RED)

    def _cfg_reset(self, _):
        self.cfg = {
            "ignore_dirs":  list(IGNORE_DIRS),
            "ignore_files": list(IGNORE_FILES),
            "include_ext":  [], "exclude_ext": [], "max_mb": 10,
        }
        self._c_idirs.value  = ", ".join(IGNORE_DIRS)
        self._c_ifiles.value = ", ".join(IGNORE_FILES)
        self._c_inc.value    = ""
        self._c_exc.value    = ""
        self._c_mb.value     = "10"
        self.page.update()
        self._snack("Configuración restaurada", GREEN)

    # ── VIEW: TIMELINE POR ARCHIVO ───────────────────────────

    def _mk_timeline(self):
        self._tl2_selected_file   = None
        self._tl2_selected_commit = None

        # Panel izquierdo: lista de archivos
        self._tl2_files = ft.Column(
            spacing=2, scroll=ft.ScrollMode.AUTO, expand=True)

        # Panel central: commits del archivo seleccionado
        self._tl2_commits_title = ft.Text(
            "Seleccioná un archivo", size=12, color=MUTED)
        self._tl2_commits = ft.Column(
            spacing=4, scroll=ft.ScrollMode.AUTO, expand=True)

        # Panel derecho: diff/snapshot del commit seleccionado
        self._tl2_diff_title = ft.Text("", size=11, color=MUTED)
        self._tl2_diff_col   = ft.Column(
            spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)

        self._tl2_empty = ft.Container(
            ft.Column([
                ft.Icon(ft.Icons.COMMIT, size=52, color=MUTED),
                ft.Text("Abrí un directorio para ver los commits",
                        size=14, color=MUTED,
                        text_align=ft.TextAlign.CENTER),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12),
            alignment=ft.alignment.center, expand=True,
        )

        panel_files = ft.Container(
            ft.Column([
                ft.Text("ARCHIVOS", size=10, color=MUTED,
                        weight=ft.FontWeight.W_600,
                        style=ft.TextStyle(letter_spacing=1.2)),
                ft.Divider(height=1, color=BORDER),
                self._tl2_files,
            ], spacing=6, expand=True),
            width=200,
            bgcolor=RAIL,
            border=ft.border.only(right=ft.BorderSide(1, BORDER)),
            padding=ft.padding.only(left=12, right=0, top=14, bottom=14),
        )

        panel_commits = ft.Container(
            ft.Column([
                self._tl2_commits_title,
                ft.Divider(height=1, color=BORDER),
                self._tl2_commits,
            ], spacing=6, expand=True),
            width=210,
            border=ft.border.only(right=ft.BorderSide(1, BORDER)),
            padding=ft.padding.symmetric(horizontal=12, vertical=14),
        )

        panel_diff = ft.Container(
            ft.Column([
                self._tl2_diff_title,
                ft.Divider(height=1, color=BORDER),
                self._tl2_diff_col,
            ], spacing=6, expand=True),
            expand=True,
            padding=ft.padding.symmetric(horizontal=14, vertical=14),
        )

        return ft.Column([
            label("TIMELINE DE ARCHIVOS"),
            divider(),
            self._tl2_empty,
            ft.Container(
                ft.Row([panel_files, panel_commits, panel_diff],
                       spacing=0, expand=True),
                expand=True, visible=True,
            ),
        ], spacing=8, expand=True)

    def _render_timeline(self):
        if not self.repo:
            return

        self._tl2_empty.visible = False
        commits_db = load_commits(self.repo)

        self._tl2_files.controls.clear()

        if not commits_db:
            self._tl2_empty.visible = True
            self.page.update()
            return

        # Agrupar por carpeta
        from collections import defaultdict as _dd
        groups = _dd(list)
        for rel in sorted(commits_db.keys()):
            parent = "/".join(rel.split("/")[:-1]) or "/"
            groups[parent].append(rel)

        for folder in sorted(groups):
            if folder != "/":
                self._tl2_files.controls.append(ft.Container(
                    ft.Row([
                        ft.Icon(ft.Icons.FOLDER_OUTLINED, size=12, color=MUTED),
                        mono(folder, size=10, color=MUTED),
                    ], spacing=5),
                    padding=ft.padding.only(top=8, bottom=2),
                ))
            for rel in groups[folder]:
                n_commits  = len(commits_db[rel])
                is_sel     = rel == self._tl2_selected_file
                has_unsaved = any(
                    c["report"] == "" for c in commits_db[rel])
                bg = ft.Colors.with_opacity(0.12, GREEN) if is_sel \
                     else ft.Colors.TRANSPARENT
                name = rel.split("/")[-1]

                self._tl2_files.controls.append(ft.Container(
                    ft.Row([
                        ft.Icon(ft.Icons.INSERT_DRIVE_FILE_OUTLINED,
                                size=13, color=MUTED),
                        ft.Column([
                            mono(name, size=11,
                                 color=GREEN if is_sel else TEXT,
                                 weight=ft.FontWeight.BOLD if is_sel
                                 else ft.FontWeight.NORMAL),
                            ft.Row([
                                mono(f"{n_commits} commit{'s' if n_commits != 1 else ''}",
                                     size=9, color=MUTED),
                                *([ badge("•", YELLOW) ] if has_unsaved else []),
                            ], spacing=4),
                        ], spacing=1, tight=True, expand=True),
                    ], spacing=6),
                    bgcolor=bg,
                    border_radius=4,
                    padding=ft.padding.symmetric(horizontal=8, vertical=6),
                    margin=ft.margin.only(right=10, bottom=2),
                    ink=True,
                    on_click=lambda _, r=rel: self._tl2_select_file(r),
                ))

        # Restaurar selección previa
        if self._tl2_selected_file and self._tl2_selected_file in commits_db:
            self._tl2_select_file(
                self._tl2_selected_file, redraw_list=False)

        self.page.update()

    def _tl2_select_file(self, rel: str, redraw_list: bool = True):
        self._tl2_selected_file   = rel
        self._tl2_selected_commit = None

        if redraw_list:
            self._render_timeline()
            return   # _render_timeline llama de nuevo a esta función

        commits  = get_file_history(self.repo, rel)
        versions = get_versions(self.repo)

        self._tl2_commits_title.value = rel.split("/")[-1]
        self._tl2_commits_title.color = GREEN
        self._tl2_commits.controls.clear()

        if not commits:
            self._tl2_commits.controls.append(
                ft.Text("Sin commits registrados", size=11, color=MUTED))
            self.page.update()
            return

        # Construir mapa tag → display para badges de versión
        tag_date = {v["tag"]: v["date"][:10] for v in versions}

        # Mostrar de más reciente a más antiguo
        for i, c in enumerate(reversed(commits)):
            idx    = len(commits) - 1 - i
            ts     = datetime.fromisoformat(c["ts"])
            t_str  = ts.strftime("%d/%m  %H:%M:%S")
            r_tag  = c.get("report", "")
            is_sel = idx == self._tl2_selected_commit

            bg       = ft.Colors.with_opacity(0.12, GREEN) if is_sel \
                       else ft.Colors.TRANSPARENT
            border_c = GREEN if is_sel else ft.Colors.TRANSPARENT

            tag_pill = ft.Container(
                ft.Text(r_tag, size=9, color=BG,
                        weight=ft.FontWeight.BOLD),
                bgcolor=GREEN if r_tag else YELLOW,
                padding=ft.padding.symmetric(horizontal=5, vertical=1),
                border_radius=3,
            ) if r_tag else ft.Container(
                ft.Text("pre", size=9, color=BG,
                        weight=ft.FontWeight.BOLD),
                bgcolor=YELLOW,
                padding=ft.padding.symmetric(horizontal=5, vertical=1),
                border_radius=3,
            )

            self._tl2_commits.controls.append(ft.Container(
                ft.Column([
                    ft.Row([
                        ft.Container(
                            width=6, height=6,
                            bgcolor=GREEN if r_tag else YELLOW,
                            border_radius=3,
                        ),
                        mono(f"#{idx + 1}", size=11, color=MUTED),
                        tag_pill,
                    ], spacing=5),
                    ft.Text(t_str, size=10, color=MUTED),
                ], spacing=2),
                bgcolor=bg,
                border=ft.border.all(1, border_c),
                border_radius=5,
                padding=ft.padding.symmetric(horizontal=8, vertical=6),
                margin=ft.margin.only(bottom=3),
                ink=True,
                on_click=lambda _, ix=idx: self._tl2_select_commit(ix),
            ))

        self._tl2_diff_title.value = "Seleccioná un commit"
        self._tl2_diff_col.controls.clear()
        self.page.update()

    def _tl2_select_commit(self, commit_idx: int):
        self._tl2_selected_commit = commit_idx
        rel     = self._tl2_selected_file
        commits = get_file_history(self.repo, rel)

        if commit_idx >= len(commits):
            return

        commit = commits[commit_idx]
        ts     = datetime.fromisoformat(commit["ts"]).strftime("%d/%m/%Y %H:%M:%S")
        r_tag  = commit.get("report", "") or "pre-reporte"

        self._tl2_diff_title.value = f"#{commit_idx + 1}  ·  {ts}  ·  {r_tag}"
        self._tl2_diff_title.color = GREEN

        # Refrescar lista de commits con nueva selección
        self._tl2_select_file(rel, redraw_list=False)

        # Calcular diff vs commit anterior
        self._tl2_diff_col.controls.clear()
        new_content = commit.get("content", "")

        if commit_idx == 0:
            # Primer commit: mostrar todo el contenido como nuevo
            self._tl2_render_full(new_content, mode="added")
        else:
            old_content = commits[commit_idx - 1].get("content", "")
            if old_content == new_content:
                self._tl2_diff_col.controls.append(
                    ft.Container(
                        ft.Row([
                            ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE,
                                    size=14, color=MUTED),
                            ft.Text("Contenido idéntico al commit anterior",
                                    size=11, color=MUTED),
                        ], spacing=6),
                        padding=ft.padding.only(bottom=8),
                    )
                )
            else:
                diff = list(difflib.unified_diff(
                    old_content.splitlines(),
                    new_content.splitlines(),
                    fromfile=f"#{commit_idx}", tofile=f"#{commit_idx + 1}",
                    lineterm="", n=3,
                ))
                stats = App._diff_stats(diff)
                self._tl2_diff_col.controls.append(ft.Container(
                    ft.Row([
                        ft.Text(f"+{stats['added']}", size=11, color=GREEN,
                                weight=ft.FontWeight.BOLD,
                                style=ft.TextStyle(font_family="monospace")),
                        ft.Text(f"-{stats['removed']}", size=11, color=RED,
                                weight=ft.FontWeight.BOLD,
                                style=ft.TextStyle(font_family="monospace")),
                    ], spacing=8),
                    padding=ft.padding.only(bottom=6),
                ))
                self._render_unified_diff(diff, target=self._tl2_diff_col)

        self.page.update()

    def _tl2_render_full(self, content: str, mode: str):
        """Muestra el contenido completo de un archivo (primer commit)."""
        lines  = content.splitlines()
        color  = BLUE if mode == "added" else TEXT
        prefix = "+" if mode == "added" else " "
        bg     = ft.Colors.with_opacity(0.06, color)

        self._tl2_diff_col.controls.append(ft.Container(
            ft.Row([
                ft.Icon(ft.Icons.ADD_CIRCLE_OUTLINE, size=14, color=BLUE),
                ft.Text("Primer commit — contenido inicial",
                        size=11, color=BLUE),
            ], spacing=6),
            padding=ft.padding.only(bottom=8),
        ))

        MAX = 300
        for i, line in enumerate(lines[:MAX], 1):
            clean = line.rstrip("\r\n") if isinstance(line, str) else line
            self._tl2_diff_col.controls.append(ft.Container(
                ft.Row([
                    ft.Container(
                        ft.Text(str(i), size=10,
                                color=ft.Colors.with_opacity(0.4, color),
                                style=ft.TextStyle(font_family="monospace")),
                        width=36, alignment=ft.alignment.center_right,
                        padding=ft.padding.only(right=4),
                    ),
                    ft.Container(
                        ft.Text(prefix, size=11, color=color,
                                weight=ft.FontWeight.BOLD,
                                style=ft.TextStyle(font_family="monospace")),
                        width=14, alignment=ft.alignment.center,
                    ),
                    ft.Text(clean, size=11, color=color,
                            style=ft.TextStyle(font_family="monospace"),
                            selectable=True, expand=True,
                            no_wrap=True,
                            overflow=ft.TextOverflow.CLIP),
                ], spacing=0, tight=True),
                bgcolor=bg,
                padding=ft.padding.symmetric(horizontal=4, vertical=1),
            ))
        if len(lines) > MAX:
            self._tl2_diff_col.controls.append(
                ft.Text(f"... {len(lines) - MAX} líneas más",
                        size=10, color=MUTED)
            )

    def _on_nav(self, e):
        self._nav_idx = e.control.selected_index
        self._content.content = self._views[self._nav_idx]
        if self._nav_idx == 1:
            self._render_history()
        elif self._nav_idx == 3:
            self._render_timeline()
        self.page.update()

    # ── Dir pick ──────────────────────────────────────────────

    def _on_dir_result(self, e: ft.FilePickerResultEvent):
        if not e.path:
            return
        self.repo = Path(e.path)
        self._dir_txt.value = str(self.repo)
        self._start_watcher()
        self._render_files()

    # ── Watcher ───────────────────────────────────────────────

    def _start_watcher(self):
        if self._observer:
            self._observer.stop()
            self._observer.join()
        self._observer = Observer()
        self._observer.schedule(
            _Watcher(
                cb=lambda: self.page.run_thread(self._render_files),
                on_file_change=self._on_file_commit,
            ),
            str(self.repo), recursive=True,
        )
        self._observer.start()

    def _on_file_commit(self, abs_path: str):
        """Llamado por el watcher al detectar un cambio — guarda commit del archivo."""
        if not self.repo:
            return
        p = Path(abs_path)
        if not p.exists() or not p.is_file():
            return
        try:
            rel  = str(p.relative_to(self.repo))
            h    = sha256(p)
            body = read_safe(p)
            push_file_commit(self.repo, rel, body, h)
            # Refrescar timeline si está visible
            if self._nav_idx == 3:
                self.page.run_thread(self._render_timeline)
        except Exception:
            pass

    # ── Save version (con modal de comentario) ────────────────

    def _do_save(self, tag: str):
        """Abre modal de comentario y luego guarda la versión."""
        tag = tag.strip()
        if not self.repo or not tag:
            self._snack("Tag vacío", RED)
            return
        if tag in {v["tag"] for v in get_versions(self.repo)}:
            self._snack(f"Tag '{tag}' ya existe", RED)
            return

        # ── Archivos que van al reporte ───────────────────────
        ch = self._live_ch
        changed_files = (
            ch.get("added", []) +
            ch.get("modified", []) +
            ch.get("deleted", [])
        )

        # ── Campo de comentario ───────────────────────────────
        comment_tf = ft.TextField(
            hint_text="Describí los cambios de esta versión... (opcional)",
            multiline=True,
            min_lines=3,
            max_lines=5,
            bgcolor=BG,
            border_color=BORDER,
            focused_border_color=GREEN,
            color=TEXT,
            hint_style=ft.TextStyle(color=MUTED),
            text_size=12,
            expand=True,
        )

        # ── Lista de archivos modificados (solo lectura) ──────
        files_col = ft.Column(spacing=3)
        if changed_files:
            for fname in sorted(changed_files):
                if fname in ch.get("added", []):
                    sym, color = "+", GREEN
                elif fname in ch.get("deleted", []):
                    sym, color = "-", RED
                else:
                    sym, color = "~", YELLOW
                files_col.controls.append(
                    ft.Row([
                        badge(sym, color),
                        mono(fname, size=11, color=MUTED),
                    ], spacing=6)
                )
        else:
            files_col.controls.append(
                ft.Text("Primer reporte — snapshot inicial",
                        size=11, color=MUTED))

        def confirm(_):
            comment = comment_tf.value.strip()
            self.page.close(modal)
            self._run_save(tag, comment)

        modal = ft.AlertDialog(
            modal=True,
            title=ft.Row([
                ft.Icon(ft.Icons.SAVE_OUTLINED, color=GREEN, size=18),
                ft.Text(f"Guardar  {tag}", color=TEXT,
                        weight=ft.FontWeight.W_600),
            ], spacing=8),
            content=ft.Container(
                ft.Column([
                    # Archivos
                    ft.Container(
                        ft.Column([
                            ft.Text("ARCHIVOS EN ESTE REPORTE",
                                    size=10, color=MUTED,
                                    weight=ft.FontWeight.W_600,
                                    style=ft.TextStyle(letter_spacing=1.2)),
                            ft.Divider(height=1, color=BORDER),
                            ft.Container(
                                content=files_col,
                                height=min(120, len(changed_files or [1]) * 28 + 10),
                            ),
                        ], spacing=6),
                        bgcolor=ft.Colors.with_opacity(0.4, SURFACE),
                        border=ft.border.all(1, BORDER),
                        border_radius=6,
                        padding=10,
                    ),
                    ft.Container(height=4),
                    # Comentario
                    ft.Text("COMENTARIO", size=10, color=MUTED,
                            weight=ft.FontWeight.W_600,
                            style=ft.TextStyle(letter_spacing=1.2)),
                    ft.Divider(height=1, color=BORDER),
                    comment_tf,
                ], spacing=8, tight=True),
                width=480,
            ),
            bgcolor=SURFACE,
            actions=[
                ft.TextButton(
                    "Cancelar",
                    style=ft.ButtonStyle(color=MUTED),
                    on_click=lambda _: self.page.close(modal),
                ),
                ft.ElevatedButton(
                    f"Guardar {tag}",
                    icon=ft.Icons.SAVE_OUTLINED,
                    style=ft.ButtonStyle(
                        bgcolor=GREEN, color=BG,
                        shape=ft.RoundedRectangleBorder(radius=6)),
                    on_click=confirm,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(modal)

    def _run_save(self, tag: str, comment: str):
        """Ejecuta el guardado con barra de progreso."""
        prog = ft.ProgressBar(width=320, color=GREEN, bgcolor=BORDER)
        lbl  = mono("Iniciando...", color=MUTED, size=12)
        dlg  = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Guardando {tag}...", color=TEXT),
            content=ft.Column([lbl, prog], spacing=10, tight=True),
            bgcolor=SURFACE,
        )
        self.page.open(dlg)

        def run():
            def upd(msg, pct):
                lbl.value  = msg
                prog.value = pct / 100
                self.page.update()
            try:
                save_version(self.repo, tag, self.cfg,
                             comment=comment, on_progress=upd)
                self.page.close(dlg)
                self._render_files()
                if self._nav_idx == 1:
                    self._render_history()
                if self._nav_idx == 3:
                    self._render_timeline()
                self._snack(f"'{tag}' guardado correctamente", GREEN)
            except Exception as ex:
                self.page.close(dlg)
                self._snack(f"Error: {ex}", RED)

        threading.Thread(target=run, daemon=True).start()

    # ── Report viewer ─────────────────────────────────────────

    def _show_report(self, tag: str):
        vdir = _vdir(self.repo)
        for v in _meta(vdir)["versions"]:
            if v["tag"] == tag:
                rp = vdir / v["report_file"]
                if not rp.exists():
                    self._snack("Archivo no encontrado", RED)
                    return
                txt = rp.read_text(encoding="utf-8")
                dlg = ft.AlertDialog(
                    title=mono(f"Reporte: {tag}", color=GREEN),
                    content=ft.Container(
                        ft.TextField(
                            value=txt, multiline=True, read_only=True,
                            min_lines=20, max_lines=30,
                            bgcolor=BG, border_color=BORDER,
                            color=TEXT, text_size=11,
                            text_style=ft.TextStyle(font_family="monospace"),
                        ),
                        width=720,
                    ),
                    bgcolor=SURFACE,
                    actions=[ft.TextButton(
                        "Cerrar",
                        style=ft.ButtonStyle(color=MUTED),
                        on_click=lambda _: self.page.close(dlg),
                    )],
                )
                self.page.open(dlg)
                return

    # ── Restore ───────────────────────────────────────────────

    def _show_restore(self, tag: str):
        dest_tf = ft.TextField(
            label="Directorio destino",
            value=str(Path.home() / f"repo_restore_{tag}"),
            bgcolor=BG, border_color=BORDER, focused_border_color=BLUE,
            color=TEXT, label_style=ft.TextStyle(color=MUTED),
            text_size=12, width=440,
        )

        def do_restore(_):
            dest = Path(dest_tf.value.strip())
            self.page.close(dlg)
            def run():
                try:
                    restore(self.repo, tag, dest)
                    self._snack(f"Restaurado en {dest.name}/", BLUE)
                except Exception as ex:
                    self._snack(f"Error: {ex}", RED)
            threading.Thread(target=run, daemon=True).start()

        dlg = ft.AlertDialog(
            title=ft.Text(f"Restaurar a '{tag}'", color=BLUE),
            content=ft.Column([
                ft.Text("Los archivos se reconstruirán en:", size=12, color=MUTED),
                dest_tf,
            ], spacing=10, tight=True),
            bgcolor=SURFACE,
            actions=[
                ft.TextButton("Cancelar", style=ft.ButtonStyle(color=MUTED),
                              on_click=lambda _: self.page.close(dlg)),
                ft.ElevatedButton("Restaurar",
                    style=ft.ButtonStyle(bgcolor=BLUE, color=BG,
                        shape=ft.RoundedRectangleBorder(radius=6)),
                    on_click=do_restore),
            ],
        )
        self.page.open(dlg)

    # ── Snackbar ──────────────────────────────────────────────

    def _snack(self, msg: str, color=TEXT):
        self.page.open(ft.SnackBar(
            ft.Text(msg, color=BG, weight=ft.FontWeight.W_500),
            bgcolor=color,
        ))

    # ── Cleanup ───────────────────────────────────────────────

    def cleanup(self):
        if self._observer:
            self._observer.stop()


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────

def main(page: ft.Page):
    app = App(page)

    def on_win(e):
        if getattr(e, "data", None) == "close":
            app.cleanup()

    page.window.on_event = on_win


ft.app(target=main)