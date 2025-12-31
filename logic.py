# logic.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import threading
from typing import List, Optional, Sequence, Tuple, Union, Callable
import subprocess

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

Cmd = Union[str, Sequence[str]]


def run_stream_lastline(
    cmd: Cmd,
    *,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    shell: bool = False,
    print_live: bool = True,
    check: bool = False,
) -> Tuple[int, Optional[str], List[str]]:
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

    last_line: Optional[str] = None
    lines: List[str] = []

    assert p.stdout is not None
    for line in p.stdout:
        line = line.rstrip("\n")
        lines.append(line)
        last_line = line
        if print_live:
            print(line, flush=True)

    rc = p.wait()

    if check and rc != 0:
        raise subprocess.CalledProcessError(rc, cmd, output="\n".join(lines))

    return rc, last_line, lines


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


DEVICES = (
    ("Google", "Pixel", "sailfish"),
    ("Google", "Pixel XL", "marlin"),
    ("Google", "Pixel 2", "walleye"),
    ("Google", "Pixel 2 XL", "taimen"),
    ("Google", "Pixel 3", "blueline"),
    ("Google", "Pixel 3 XL", "crosshatch"),
    ("Google", "Pixel 3a", "sargo"),
    ("Google", "Pixel 3a XL", "bonito"),
    ("Google", "Pixel 4", "flame"),
    ("Google", "Pixel 4 XL", "coral"),
    ("Google", "Pixel 4a", "sunfish"),
    ("Google", "Pixel 4a 5G", "bramble"),
    ("Google", "Pixel 5", "redfin"),
    ("Google", "Pixel 5a", "barbet"),
    ("Google", "Pixel 6", "oriole"),
    ("Google", "Pixel 6 Pro", "raven"),
    ("Google", "Pixel 6a", "bluejay"),
    ("Google", "Pixel 7", "panther"),
    ("Google", "Pixel 7 Pro", "cheetah"),
    ("Google", "Pixel 7a", "lynx"),
    ("Google", "Pixel 8", "shiba"),
    ("Google", "Pixel 8 Pro", "husky"),
    ("Google", "Pixel 8a", "akita"),
    ("Google", "Pixel 9", "tokay"),
    ("Google", "Pixel 9 Pro", "caiman"),
    ("Google", "Pixel 9 Pro Fold", "comet"),
    ("Google", "Pixel 9 Pro XL", "komodo"),
    ("Google", "Pixel 9a", "tegu"),
    ("Google", "Pixel Fold", "felix"),
    ("Google", "Pixel Tablet", "tangorpro"),
    ("Samsung", "Galaxy A21s", "a21s"),
    ("Samsung", "Galaxy A52 4G", "a52q"),
    ("Samsung", "Galaxy A52s 5G", "a52sxq"),
    ("Samsung", "Galaxy A71", "a71"),
    ("Samsung", "Galaxy A72", "a72q"),
    ("Samsung", "Galaxy A73 5G", "a73xq"),
    ("Samsung", "Galaxy Note10", "d1"),
    ("Samsung", "Galaxy Note10 5G", "d1x"),
    ("Samsung", "Galaxy Note10+", "d2s"),
    ("Samsung", "Galaxy Note10+ 5G", "d2x"),
    ("Samsung", "Galaxy S10e", "beyond0lte"),
    ("Samsung", "Galaxy S10", "beyond1lte"),
    ("Samsung", "Galaxy S10 5G", "beyondx"),
    ("Samsung", "Galaxy S10+", "beyond2lte"),
    ("Samsung", "Galaxy S20 (4G/5G", "x1s"),
    ("Samsung", "Galaxy S20+", "y2s"),
    ("Samsung", "Galaxy S20 FE (Exynos)", "r8s"),
    ("Samsung", "Galaxy S20 Ultra (5G)", "z3s"),
    ("Samsung", "Galaxy Tab A 8.0 (2019)", "gtowifi"),
    ("Samsung", "Tab A7 10.4 (2020) (LTE)", "gta4l"),
    ("Samsung", "Tab A7 10.4 (2020) (Wi-Fi)", "gta4lwifi"),
    ("Samsung", "Galaxy Tab S5e (LTE)", "gts4lv"),
    ("Samsung", "Galaxy Tab S5e (Wi-Fi)", "gts4lvwifi"),
    ("Samsung", "Galaxy Tab S6 Lite (LTE)", "gta4xl"),
    ("Samsung", "Galaxy Tab S7 (LTE)", "gts7l"),
    ("Samsung", "Galaxy Tab S7 (Wi-Fi)", "gts7lwifi"),
    ("OnePlus", "OnePlus 5", "cheeseburger"),
    ("OnePlus", "OnePlus 5T", "dumpling"),
    ("OnePlus", "OnePlus 6", "enchilada"),
    ("OnePlus", "OnePlus 6T", "fajita"),
    ("OnePlus", "OnePlus 7", "guacamoleb"),
    ("OnePlus", "OnePlus 7 Pro", "guacamole"),
    ("OnePlus", "OnePlus 7T", "hotdogb"),
    ("OnePlus", "OnePlus 7T Pro", "hotdog"),
    ("OnePlus", "OnePlus 8", "instantnoodle"),
    ("OnePlus", "OnePlus 8 Pro", "instantnoodlep"),
    ("OnePlus", "OnePlus 8T", "kebab"),
    ("OnePlus", "OnePlus 9", "lemonade"),
    ("OnePlus", "OnePlus 9 Pro", "lemonadep"),
    ("OnePlus", "OnePlus 9R", "lemonades"),
    ("OnePlus", "OnePlus 9RT", "martini"),
    ("OnePlus", "OnePlus 11 5G", "salami"),
    ("OnePlus", "OnePlus 12", "waffle"),
    ("OnePlus", "OnePlus 13", "dodge"),
    ("OnePlus", "OnePlus Ace V3", "audi"),
    ("OnePlus", "OnePlus Ace 3 Pro", "corvette"),
    ("OnePlus", "OnePlus Nord 4", "avalon"),
    ("OnePlus", "Nord CE 2 Lite SG", "oscaro"),
    ("OnePlus", "OnePlus Nord CE 4", "benz"),
    ("OnePlus", "OnePlus Nord N20", "gunnar"),
    ("OnePlus", "OnePlus Nord N200", "dre"),
    ("Xiaomi", "Xiaomi 12", "cupid"),
    ("Xiaomi", "Xiaomi 12 Pro", "zeus"),
    ("Xiaomi", "Xiaomi 12S", "mayfly"),
    ("Xiaomi", "Xiaomi 12S Pro", "unicorn"),
    ("Xiaomi", "Xiaomi 12S Ultra", "thor"),
    ("Xiaomi", "Xiaomi 13", "fuxi"),
    ("Xiaomi", "Xiaomi 13 Pro", "nuwa"),
    ("Xiaomi", "BlackShark", "shark"),
    ("Xiaomi", "Mi 10", "umi"),
    ("Xiaomi", "Mi 10 Pro", "cmi"),
    ("Xiaomi", "Mi 10S", "thyme"),
    ("Xiaomi", "Mi 11 Lite 5G", "renoir"),
    ("Xiaomi", "Mi 5", "gemeni"),
    ("Xiaomi", "Xiaomi Mi 5s Plus", "natrium"),
    ("Xiaomi", "Mi 6", "sagit"),
    ("Xiaomi", "Mi 8", "dipper"),
    ("Xiaomi", "Mi 8 Explorer Edition", "ursa"),
    ("Xiaomi", "Mi 8 Pro", "equuleus"),
    ("Xiaomi", "Mi 9 SE", "grus"),
    ("Xiaomi", "Mi A3", "laurel_sprout"),
    ("Xiaomi", "Mi CC9 Meitu Edition", "vela"),
    ("Xiaomi", "Mi MIX 2", "chiron"),
    ("Xiaomi", "Mi MIX 2S", "polaris"),
    ("Xiaomi", "Mi MIX 3", "perseus"),
    ("Xiaomi", "Mi MIX Fold 2", "zizhan"),
    ("Xiaomi", "POCO F1", "beryllium"),
    ("Xiaomi", "POCO X3 NFC", "surya"),
    ("Xiaomi", "POCO X3 Pro", "vayu"),
    ("Xiaomi", "K60 Pro", "socrates"),
    ("Xiaomi", "Redmi 7 Pro", "violet"),
    ("Xiaomi", "Redmi Note 10 Pro (Global)", "sweet"),
    ("Nothing", "Phone (1)", "Spacewar"),
    ("Nothing", "Phone (2)", "Pong"),
    ("Asus", "ASUS Zenfone 5Z (ZS620KL)", "Z01R"),
    ("Asus", "ZenFone 8", "sake"),
    ("Lenovo", "Z5 Pro 5G", "heart"),
    ("Lenovo", "Z6 Pro", "zippo"),
    ("Motorola", "defy 2021", "bathena"),
    ("Motorola", "edge 20", "berlin"),
    ("Motorola", "edge 20 pro", "pstar"),
    ("Motorola", "edge 2024", "avatrn"),
    ("Motorola", "edge 30", "dubai"),
    ("Motorola", "edge 30 fusion", "tundra"),
    ("Motorola", "edge 30 neo", "miami"),
    ("Motorola", "edge 30 ultra", "eqs"),
    ("Motorola", "moto g 5G (2024)", "fogo"),
    ("Motorola", "moto g power (2021)", "borneo"),
    ("Motorola", "moto g stylus 5G", "denver"),
    ("Motorola", "moto g stylus 5G (2022)", "milanf"),
    ("Motorola", "moto g32", "devon"),
    ("Motorola", "moto g42", "hawao"),
    ("Motorola", "moto g52", "rhode"),
    ("Motorola", "moto g6 plus", "evert"),
    ("Motorola", "moto g7", "river"),
    ("Motorola", "moto g7 play", "channel"),
    ("Motorola", "moto g7 plus", "lake"),
    ("Motorola", "moto g7 power", "ocean"),
    ("Motorola", "moto g82 5G", "rhodep"),
    ("Motorola", "moto g84 5G", "bangkk"),
    ("Motorola", "x4", "payton"),
    ("Motorola", "z3", "messi"),
    ("Motorola", "z3 play", "backham"),
    ("Motorola", "one action", "troika"),
    ("Motorola", "ThinkPhone by motorola", "bronco"),
    ("Nokia", "Nokia 6.1 (2018)", "PL2"),
    ("Nokia", "Nokia 7 plus", "B2N"),
    ("Nokia", "Nokia 8", "NB1"),
    ("Realme", "Realme 10 Pro 5G", "luigi"),
    ("SHIFT", "SHIFT6mq", "axolotl"),
)

BRANDS = [
    "Samsung",
    "Google",
    "OnePlus",
    "Xiaomi",
    "Nothing",
    "Asus",
    "Motorola",
    "Nokia",
    "SHIFT",
    "Realme",
]

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
