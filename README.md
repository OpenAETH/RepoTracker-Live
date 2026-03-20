# RepoTracker Live 🛰️

**Estación de desarrollo pre-git para artesanías de código.**

Control de versiones artesanal por reportes `.txt` — con watcher en vivo, diff visual, timeline de commits por archivo y auditoría completa, sin necesidad de git ni infraestructura externa.

---

## ¿Qué es?

RepoTracker Live es una app de escritorio (Flet) que observa un directorio de código y te da control de versiones completo mediante reportes de texto plano. Cada reporte es un snapshot legible por humanos: árbol de directorios, métricas, diffs y contenido completo de todos los archivos.

Diseñada para flujos de trabajo artesanales donde querés trazabilidad sin la fricción de git — iterar rápido, entender qué cambió y cuándo, y poder reconstruir cualquier estado anterior.

---

## Instalación

```bash
pip install flet==0.28.3 watchdog
python repotracker-live-flet.py
```

**Requisitos:** Python 3.10+

---

## Uso rápido

1. Abrí un directorio con **Abrir directorio**
2. La app detecta los archivos automáticamente
3. Generá el primer reporte — aparece un banner con el tag sugerido (`v1`)
4. Modificá archivos normalmente — el watcher detecta cada guardado
5. Cuando estés listo, generá el siguiente reporte desde el banner

---

## Vistas

### 📁 Archivos
Vista del directorio observado con estado en tiempo real. Los archivos modificados desde el último reporte aparecen con badge de color:

| Badge | Significado |
|-------|-------------|
| `~` amarillo | Modificado desde la última versión |
| `+` azul | Archivo nuevo (no estaba en la versión anterior) |
| `-` rojo tachado | Eliminado |

El banner superior propone generar reporte cuando hay cambios. El tag se auto-incrementa (`v1`, `v2`, `v3`...) y es editable antes de confirmar.

### 🕐 Historial
Timeline de versiones guardadas con tres paneles:

- **Versiones** — lista cronológica con badges de cambios por versión. Siempre incluye un ítem `pre-vN` (EN VIVO) que muestra los cambios actuales sin guardar
- **Archivos** — al seleccionar una versión, muestra qué archivos cambiaron y cómo (`+` nuevo, `~` modificado, `-` eliminado)
- **Diff** — al seleccionar un archivo, muestra el diff visual con números de línea, colores por operación y contexto

El ítem **pre-vN** se actualiza en tiempo real con cada cambio detectado por el watcher. Permite ver los diffs actuales antes de decidir si guardar un reporte.

### 〜 Timeline
Historial de auto-commits por archivo individual — independiente de los reportes.

El watcher registra un commit automático cada vez que detecta un guardado en un archivo (con debounce de 0.8s). Cada commit almacena el snapshot completo y el diff respecto al commit anterior.

- **Archivos** — lista de archivos con cantidad de commits y badge `•` si tienen commits pendientes de vincular a un reporte
- **Commits** — línea de tiempo de todos los saves del archivo, con número de commit (`#1`, `#2`...), timestamp, badge del reporte al que pertenece (`v1`, `v2`, `pre`)
- **Diff** — diff visual del commit seleccionado vs el anterior. El primero muestra el contenido completo como snapshot inicial

Los commits se persisten en `~/.repo_versions/<dir>/file_commits.json`. Al generar un reporte, los commits `pre` se vinculan automáticamente al tag generado.

### ⚙️ Config
Filtros configurables que aplican a la generación de reportes y a la vista de archivos:

| Campo | Descripción |
|-------|-------------|
| Extensiones incluir | Lista separada por comas — vacío = todas. Ej: `.py, .js, .ts` |
| Extensiones excluir | Adicionales a las binarias. Ej: `.log, .tmp` |
| Carpetas ignoradas | Por defecto: `__pycache__`, `.git`, `node_modules`, `venv`, etc. |
| Archivos ignorados | Por defecto: `.DS_Store`, `.env` |
| Tamaño máximo (MB) | `0` = sin límite |

La configuración se persiste en `~/.repo_tracker_config.json` al hacer **Guardar configuración**.

---

## Formato de reportes `.txt`

Cada reporte guardado en `~/.repo_versions/<directorio>/` tiene esta estructura:

```
================================================================================
REPORTE: v2
DIRECTORIO: /ruta/al/proyecto
FECHA: 2026-03-19T19:37:00
================================================================================

ÁRBOL DEL REPOSITORIO
  (árbol visual completo con └── ├── │)

CAMBIOS RESPECTO A VERSIÓN ANTERIOR   ← solo en v2+
  [+] archivo_nuevo.py
  [~] archivo_modificado.py
  [-] archivo_eliminado.py

DIFFS DE ARCHIVOS MODIFICADOS         ← solo en v2+, sección separada
  (diff unidiff por archivo)

MÉTRICAS                              ← solo en v1
  Archivos por extensión: ...
  Carpetas y cantidad de archivos: ...
  Filtros aplicados: ...

SNAPSHOT DE ARCHIVOS                  ← siempre completo
  (contenido actual de todos los archivos)
```

Los reportes son texto plano legible sin la app. El snapshot siempre incluye todos los archivos — no solo los que cambiaron — lo que permite reconstruir cualquier versión desde el `.txt` solo.

---

## Persistencia

```
~/.repo_versions/
└── <nombre_dir>_<hash8>/
    ├── meta.json          # versiones con tags, fechas y comentarios
    ├── v1.txt             # reporte completo
    ├── v1_index.json      # índice SHA256 + mtime + contenido
    ├── v2.txt
    ├── v2_index.json
    └── file_commits.json  # auto-commits del watcher por archivo

~/.repo_tracker_config.json  # filtros globales
```

El `_index.json` de cada versión almacena hash SHA256, mtime y contenido completo por archivo — permite calcular diffs entre versiones sin leer los `.txt`.

---

## Flujo típico

```
Abrir directorio
  └─ Generar v1 (snapshot inicial)
       └─ Modificar archivos normalmente
            └─ Watcher registra auto-commits en file_commits.json
                 └─ Banner propone "Generar reporte de iteración (v2)"
                      └─ Modal: escribir comentario + confirmar tag
                           └─ Reporte v2 guardado
                                └─ Auto-commits marcados como "v2"
                                     └─ pre-v3 aparece en historial listo
```

En el **Timeline**, cada archivo tiene su propia historia granular de todos los saves — incluso los que nunca llegaron a un reporte.

---

## Reconstrucción

Desde la pestaña **Historial**, cualquier versión puede restaurarse a un directorio destino. El contenido de cada archivo se recupera del `_index.json` correspondiente.

---

## Dependencias

| Paquete | Versión | Uso |
|---------|---------|-----|
| `flet` | 0.28.3 | UI de escritorio |
| `watchdog` | cualquiera | Detección de cambios en el sistema de archivos |

El resto son módulos de la biblioteca estándar de Python: `difflib`, `hashlib`, `json`, `threading`, `pathlib`.

---

## Extensiones binarias ignoradas (siempre)

`.png` `.jpg` `.jpeg` `.gif` `.webp` `.ico` `.svg` `.exe` `.dll` `.so` `.zip` `.tar` `.gz` `.7z` `.db` `.sqlite` `.pyc` `.pyd`

---

*Artesanías de código — hecho a mano, guardado con intención.* 🥰💛🚀
