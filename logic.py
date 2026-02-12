          
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import threading
from typing import List, Optional, Sequence, Tuple, Union, Callable, Any
import subprocess

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

Cmd = Union[str, Sequence[str]]

@dataclass
class CommandResult:
                                        

    rc: int
    lines: List[str]

    @property
    def last_line(self) -> Optional[str]:
        return self.lines[-1] if self.lines else None


def run_stream(
    cmd: Cmd,
    *,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    shell: bool = False,
    on_line: Optional[Callable[[str], None]] = None,
) -> CommandResult:
           
    lines: List[str] = []
    try:
        p = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            shell=shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as e:
        msg = f"ERROR: {e}"
        lines.append(msg)
        if on_line:
            on_line(msg)
        return CommandResult(rc=127, lines=lines)

    assert p.stdout is not None
    for raw in p.stdout:
        line = raw.rstrip("\n")
        lines.append(line)
        if on_line:
            on_line(line)

    rc = p.wait()
    return CommandResult(rc=rc, lines=lines)



def run_stream_lastline(
    cmd: Cmd,
    *,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    shell: bool = False,
    print_live: bool = True,
    check: bool = False,
) -> Tuple[int, Optional[str], List[str]]:
                                                                       

    res = run_stream(
        cmd,
        cwd=cwd,
        env=env,
        shell=shell,
        on_line=(lambda ln: print(ln, flush=True)) if print_live else None,
    )

    if check and res.rc != 0:
        raise subprocess.CalledProcessError(res.rc, cmd, output="\n".join(res.lines))

    return res.rc, res.last_line, res.lines

def adb_reboot_system() -> Tuple[int, Optional[str], List[str]]:
    return run_stream_lastline(["adb", "reboot"], print_live=False)


def adb_reboot_recovery() -> Tuple[int, Optional[str], List[str]]:
    return run_stream_lastline(["adb", "reboot", "recovery"], print_live=False)


def adb_reboot_fastboot() -> Tuple[int, Optional[str], List[str]]:
    return run_stream_lastline(["adb", "reboot", "bootloader"], print_live=False)


def adb_reboot_download() -> Tuple[int, Optional[str], List[str]]:
    return run_stream_lastline(["adb", "reboot", "download"], print_live=False)


UA = "LineageOS Downloader FOSS"
MIRRORBITS_FULL = "https://mirrorbits.lineageos.org/full"

ARCHIVE_BASE = "https://lineage-archive.timschumi.net"
ARCHIVE_BUILDS_API = f"{ARCHIVE_BASE}/api/builds"
ARCHIVE_FILE_BASES = (
    "https://b4.timschumi.net/lineage-archive",
    "https://lineage-archive.timschumi.net",
)



                                                                   
_DENY_PREFIXES: Tuple[str, ...] = (
    "vendor_boot",
    "init_boot",
    "vbmeta",
    "dtbo",
    "super",
    "bootloader",
)

def classify_flash_image(filename: str) -> tuple[Optional[str], Optional[str]]:
                                                                                     
    base = (filename or "").strip().lower()
    if not base:
        return None, None

    if base.endswith(".img"):
        for pfx in _DENY_PREFIXES:
            if base.startswith(pfx):
                return None, None

    if base == "boot.img" or base.endswith("-boot.img"):
        return "boot", "boot.img"

    if base == "recovery.img" or base.endswith("-recovery.img"):
        return "recovery", "recovery.img"

    if base.endswith(".img") and "recovery" in base:
        return "recovery", "recovery.img"

    return None, None


def adb_getprop(prop: str) -> str:
                                                                                      
    rc, last, _lines = run_stream_lastline(
        ["adb", "shell", "getprop", prop],
        print_live=False,
    )
    if rc != 0:
        return ""

    val = (last or "").strip()
    if not val:
        return ""

    low = val.lower()
    if low.startswith("error:"):
        return ""
    if "no devices" in low or "device offline" in low or "unauthorized" in low:
        return ""
    return val


def adb_connected_codename() -> str:
                                                                         
    for prop in ("ro.build.product", "ro.product.device"):
        v = adb_getprop(prop)
        if v:
            return v
    return ""

def archive_builds() -> list[dict]:
    with _session() as s:
        r = s.get(ARCHIVE_BUILDS_API, timeout=60)
        r.raise_for_status()
        j = r.json()

    if isinstance(j, dict) and "builds" in j:
        j = j.get("builds")

    if not isinstance(j, list):
        raise RuntimeError("Unexpected archive API response")

    return [x for x in j if isinstance(x, dict)]


def archive_devices() -> list[str]:
    builds = archive_builds()
    return sorted(
        {(b.get("device") or "").strip() for b in builds if (b.get("device") or "").strip()}
    )


_ARCH_DATE_RE = re.compile(r"-(\d{8})-")
_ARCH_VER_RE = re.compile(r"^lineage-(\d+)\.(\d+)-")


def _archive_date_from_filename(fn: str) -> int:
    m = _ARCH_DATE_RE.search(fn or "")
    if not m:
        return 0
    try:
        return int(m.group(1))
    except Exception:
        return 0


def _archive_version_from_filename(fn: str) -> tuple[int, int]:
    m = _ARCH_VER_RE.match(fn or "")
    if not m:
        return (0, 0)
    try:
        return (int(m.group(1)), int(m.group(2)))
    except Exception:
        return (0, 0)


def _archive_build_sort_key(b: dict) -> tuple[int, tuple[int, int], int]:
    fn = (b.get("filename") or b.get("name") or "").strip()
    date = _archive_date_from_filename(fn)
    ver = _archive_version_from_filename(fn)

    for k in ("datetime", "timestamp", "time"):
        v = b.get(k)
        if v is None:
            continue
        try:
            if isinstance(v, str) and v.isdigit():
                return (int(v), ver, date)
            if isinstance(v, (int, float)):
                return (int(v), ver, date)
        except Exception:
            pass

    return (date, ver, 0)


def _archive_candidate_urls(filename: str, build_id: object | None) -> list[str]:
    urls: list[str] = []
    fn = (filename or "").lstrip("/")
    for base in ARCHIVE_FILE_BASES:
        urls.append(f"{base}/{fn}")
    if build_id is not None:
        urls.append(f"{ARCHIVE_BASE}/build/{build_id}/download")
    return urls


def latest_archive_build(device: str, *, max_head_tries: int = 3) -> dict:
    device = (device or "").strip()
    if not device:
        raise RuntimeError("Missing device")

    builds = [
        b for b in archive_builds() if (b.get("device") or "").strip() == device
    ]
    if not builds:
        raise RuntimeError(f"No archive builds found for device='{device}'")

    builds.sort(key=_archive_build_sort_key, reverse=True)

    last_err: str | None = None
    with _session() as s:
        for b in builds[: max_head_tries or 3]:
            filename = (b.get("filename") or b.get("name") or "").strip()
            if not filename:
                continue

            build_id = b.get("id")
            for url in _archive_candidate_urls(filename, build_id):
                try:
                    r = s.head(url, allow_redirects=True, timeout=20)
                    if r.status_code < 400:
                        return {"url": url, "filename": filename, "source": "archive", "raw": b}
                    last_err = f"HTTP {r.status_code} for {url}"
                except Exception as e:
                    last_err = str(e)

    raise RuntimeError(last_err or "Could not locate a downloadable archive URL")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    retry = Retry(
        total=6,
        connect=6,
        read=6,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def nightly_builds(device: str) -> list[dict]:
    url = f"https://download.lineageos.org/api/v1/{device}/nightly/0"
    with _session() as s:
        r = s.get(url, timeout=30)
        r.raise_for_status()
        j = r.json()
    builds = j.get("response") or []
    if not builds:
        raise RuntimeError(f"Keine Nightly-Builds gefunden für device='{device}'")
    builds.sort(key=lambda b: int(b.get("datetime") or 0), reverse=True)
    return builds


def latest_vbmeta_via_mirrorbits(device: str, *, max_tries: int = 12) -> dict:
    return _find_mirrorbits_artifact(device, "vbmeta.img", max_tries=max_tries)


def latest_nightly(device: str) -> dict:
    return nightly_builds(device)[0]


_DATE_RE = re.compile(r"-(\d{8})-")


def _extract_yyyymmdd_from_filename(filename: str) -> Optional[str]:
    if not filename:
        return None
    m = _DATE_RE.search(filename)
    return m.group(1) if m else None


def _find_mirrorbits_artifact(
    device: str,
    artifact_name: str,
    *,
    max_tries: int = 12,
) -> dict:
    builds = nightly_builds(device)

    tried = 0
    with _session() as s:
        for b in builds:
            if tried >= max_tries:
                break

            date = _extract_yyyymmdd_from_filename(b.get("filename") or "")
            if not date:
                continue

            url = f"{MIRRORBITS_FULL}/{device}/{date}/{artifact_name}"

            try:
                r = s.head(url, allow_redirects=True, timeout=20)
                if r.status_code < 400:
                    return {
                        "url": url,
                        "filename": artifact_name,
                        "date": date,
                        "source": "mirrorbits",
                    }
            except requests.RequestException:
                pass

            tried += 1

    raise RuntimeError(
        f"Could not locate {artifact_name} on mirrorbits for device='{device}'. "
        f"Tried up to {min(max_tries, len(builds))} recent build dates."
    )


def latest_recovery_via_mirrorbits(device: str, *, max_tries: int = 12) -> dict:
    return _find_mirrorbits_artifact(device, "recovery.img", max_tries=max_tries)


def latest_boot_via_mirrorbits(device: str, *, max_tries: int = 12) -> dict:
    return _find_mirrorbits_artifact(device, "boot.img", max_tries=max_tries)


def latest_recovery_or_boot_for_device(
    *,
    is_pixel: bool,
    codename: str,
    max_tries: int = 12,
) -> dict:
    if is_pixel:
        return latest_boot_via_mirrorbits(codename, max_tries=max_tries)

    try:
        return latest_recovery_via_mirrorbits(codename, max_tries=max_tries)
    except RuntimeError:
        return latest_boot_via_mirrorbits(codename, max_tries=max_tries)


def latest_magisk_apk() -> dict:
    api = "https://api.github.com/repos/topjohnwu/Magisk/releases/latest"

    with _session() as s:
        r = s.get(api, timeout=30)
        r.raise_for_status()
        j = r.json()

    tag = (j.get("tag_name") or "").strip()
    if not tag:
        raise RuntimeError("Missing tag_name")

    assets = j.get("assets") or []
    apk = None

    for a in assets:
        name = a.get("name") or ""
        if name.startswith("Magisk-") and name.lower().endswith(".apk"):
            apk = a
            break

    if apk is None:
        for a in assets:
            name = a.get("name") or ""
            if name.lower().endswith(".apk"):
                apk = a
                break

    if apk is None:
        raise RuntimeError("No Magisk APK asset found")

    url = (apk.get("browser_download_url") or "").strip()
    filename = (apk.get("name") or "").strip()
    if not url or not filename:
        raise RuntimeError("Missing APK download url or filename")

    return {
        "tag": tag,
        "filename": filename,
        "url": url,
        "release_page": f"https://github.com/topjohnwu/Magisk/releases/tag/{tag}",
    }


import csv as _csv

_DATA_PATH = Path(__file__).with_name("devices.csv")

def _load_devices_csv(path: Path = _DATA_PATH) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    with path.open(newline="", encoding="utf-8") as f:
        r = _csv.DictReader(f)
        for row in r:
            brand = (row.get("brand") or "").strip()
            model = (row.get("model") or "").strip()
            codename = (row.get("codename") or "").strip()
            if not brand or not model:
                continue
            rows.append((brand, model, codename))
    return rows

DEVICES: tuple[tuple[str, str, str], ...] = tuple(_load_devices_csv())

def _unique_brands_in_order(devs: tuple[tuple[str, str, str], ...]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for b, _m, _c in devs:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out

BRANDS: list[str] = _unique_brands_in_order(DEVICES)


MODELS_BY_BRAND: dict[str, list[str]] = {b: [] for b in BRANDS}
CODENAME_BY_BRAND_MODEL: dict[tuple[str, str], str] = {}

for brand, model, codename in DEVICES:
    MODELS_BY_BRAND.setdefault(brand, []).append(model)
    CODENAME_BY_BRAND_MODEL[(brand, model)] = codename


def get_suggestions(brand: str, typed: str) -> list[str]:
    typed_l = (typed or "").strip().lower()
    models = MODELS_BY_BRAND.get(brand, [])
    if not models:
        return []
    if not typed_l:
        return models[:200]
    return [m for m in models if typed_l in m.lower()][:15]


@dataclass(frozen=True)
class DownloadProgress:
    done: int
    total: Optional[int]


class DownloadCallbacks:
    def __init__(
        self,
        *,
        on_progress: Callable[[DownloadProgress], None],
        on_done: Callable[[Path], None],
        on_error: Callable[[str], None],
        on_cancelled: Callable[[], None],
    ):
        self.on_progress = on_progress
        self.on_done = on_done
        self.on_error = on_error
        self.on_cancelled = on_cancelled


def download_with_progress(
    url: str,
    out_path: Path,
    *,
    stop_event: threading.Event,
    cb: DownloadCallbacks,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".part")

    try:
        with _session() as s:
            with s.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                total = r.headers.get("Content-Length")
                total_int = int(total) if total and total.isdigit() else None

                done = 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if stop_event.is_set():
                            try:
                                if tmp.exists():
                                    tmp.unlink()
                            except Exception:
                                pass
                            cb.on_cancelled()
                            return out_path

                        if chunk:
                            f.write(chunk)
                            done += len(chunk)
                            cb.on_progress(DownloadProgress(done=done, total=total_int))

        tmp.replace(out_path)
        cb.on_done(out_path)
        return out_path

    except Exception as e:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        cb.on_error(str(e))
        return out_path
