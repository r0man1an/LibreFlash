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
    ("Google", "ADT-3", "deadpool"),
    ("Google", "Chromecast Google TV (4K)", "sabrina"),
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
    ("Samsung", "Galaxy S20 FE (Snapdragon)", "r8q"),
    ("Samsung", "Galaxy S20 FE 5G", "r8q"),
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
    ("OnePlus", "OnePlus 12R / Ace3", "aston"),
    ("OnePlus", "OnePlus 13", "dodge"),
    ("OnePlus", "OnePlus Ace V3", "audi"),
    ("OnePlus", "OnePlus Ace 3 Pro", "corvette"),
    ("OnePlus", "OnePlus Nord 4", "avalon"),
    ("OnePlus", "OnePlus Nord CE 2 Lite SG", "oscaro"),
    ("OnePlus", "OnePlus Nord CE 3 Lite 5G", "larry"),
    ("OnePlus", "OnePlus Nord N30 5G", "larry"),
    ("OnePlus", "OnePlus Nord CE 4", "benz"),
    ("OnePlus", "OnePlus Nord N20", "gunnar"),
    ("OnePlus", "OnePlus Nord N200", "dre"),
    ("OnePlus", "OnePlus Pad Pro / Pad 2", "caihong"),
    ("OnePlus", "OnePlus Pad 2 Pro / Pad 3", "erhai"),
    ("Xiaomi", "Xiaomi 11 Lite 5G NE", "lisa"),
    ("Xiaomi", "Xiaomi 11 Lite NE 5G", "lisa"),
    ("Xiaomi", "Xiaomi 11 LE", "lisa"),
    ("Xiaomi", "Xiaomi 12", "cupid"),
    ("Xiaomi", "Xiaomi 12 Pro", "zeus"),
    ("Xiaomi", "Xiaomi 12S", "mayfly"),
    ("Xiaomi", "Xiaomi 12S Pro", "unicorn"),
    ("Xiaomi", "Xiaomi 12S Ultra", "thor"),
    ("Xiaomi", "Xiaomi 12T Pro", "diting"),
    ("Xiaomi", "Redmi K50 Ultra", "diting"),
    ("Xiaomi", "Xiaomi 13", "fuxi"),
    ("Xiaomi", "Xiaomi 13 Pro", "nuwa"),
    ("Xiaomi", "BlackShark", "shark"),
    ("Xiaomi", "Mi 10", "umi"),
    ("Xiaomi", "Mi 10 Pro", "cmi"),
    ("Xiaomi", "Mi 10S", "thyme"),
    ("Xiaomi", "Mi 10T", "apollon"),
    ("Xiaomi", "Mi 10T Pro", "apollon"),
    ("Xiaomi", "Redmi K30S Ultra", "apollon"),
    ("Xiaomi", "Mi 10T Lite 5G", "gauguin"),
    ("Xiaomi", "Mi 10i 5G", "gauguin"),
    ("Xiaomi", "Redmi Note 9 Pro 5G", "gauguin"),
    ("Xiaomi", "Mi 11 Lite 5G", "renoir"),
    ("Xiaomi", "Mi 11i", "haydn"),
    ("Xiaomi", "Mi 11X Pro", "haydn"),
    ("Xiaomi", "Redmi K40 Pro", "haydn"),
    ("Xiaomi", "Redmi K40 Pro+", "haydn"),
    ("Xiaomi", "Mi 9T", "davinci"),
    ("Xiaomi", "Redmi K20 China & India", "davinci"),
    ("Xiaomi", "Mi 5", "gemeni"),
    ("Xiaomi", "Mi 5s Plus", "natrium"),
    ("Xiaomi", "Mi 6", "sagit"),
    ("Xiaomi", "Mi 8", "dipper"),
    ("Xiaomi", "Mi 8 Explorer Edition", "ursa"),
    ("Xiaomi", "Mi 8 Pro", "equuleus"),
    ("Xiaomi", "Mi 9 SE", "grus"),
    ("Xiaomi", "Mi Note 10", ""),
    ("Xiaomi", "Mi Note 10 Pro", ""),
    ("Xiaomi", "Mi CC9 Pro", "tucana"),
    ("Xiaomi", "Mi A3", "laurel_sprout"),
    ("Xiaomi", "Mi CC9 Meitu Edition", "vela"),
    ("Xiaomi", "Mi MIX 2", "chiron"),
    ("Xiaomi", "Mi MIX 2S", "polaris"),
    ("Xiaomi", "Mi MIX 3", "perseus"),
    ("Xiaomi", "Mi MIX Fold 2", "zizhan"),
    ("Xiaomi", "POCO F1", "beryllium"),
    ("Xiaomi", "POCO F2 Pro", "lmi"),
    ("Xiaomi", "Redmi K30 Pro", "lmi"),
    ("Xiaomi", "POCO F3", "alioth"),
    ("Xiaomi", "Redmi K40", "alioth"),
    ("Xiaomi", "Mi 11X", "alioth"),
    ("Xiaomi", "POCO F4", "munch"),
    ("Xiaomi", "Redmi K40S", "munch"),
    ("Xiaomi", "POCO F5", "marble"),
    ("Xiaomi", "Redmi Note 12 Turbo", "marble"),
    ("Xiaomi", "POCO F5 Pro", "mondrian"),
    ("Xiaomi", "Redmi K60", "mondrian"),
    ("Xiaomi", "POCO F6 Pro", "vermeer"),
    ("Xiaomi", "Redmi K70", "vermeer"),
    ("Xiaomi", "POCO M2 Pro", "miatoll"),
    ("Xiaomi", "Redmi Note 9S", "miatoll"),
    ("Xiaomi", "Redmi Note 9 Pro / Pro Max", "miatoll"),
    ("Xiaomi", "Redmi Note 10 Lite", "miatoll"),
    ("Xiaomi", "POCO X3 NFC", "surya"),
    ("Xiaomi", "POCO X3 Pro", "vayu"),
    ("Xiaomi", "Redmi K60 Pro", "socrates"),
    ("Xiaomi", "Redmi 12C / 12C NFC", "earth"),
    ("Xiaomi", "POCO C55", "earth"),
    ("Xiaomi", "Redmi 3S / 3X / 4(X)", "Mi8937"),
    ("Xiaomi", "Redmi 5A Prime", "Mi8937"),
    ("Xiaomi", "Redmi Y1 Prime", "Mi8937"),
    ("Xiaomi", "Redmi 4A", "Mi8917"),
    ("Xiaomi", "Redmi 5A / 5A Lite / Y1 Lite", "Mi8917"),
    ("Xiaomi", "Redmi 7A / 8A / 8A Lite", "Mi439"),
    ("Xiaomi", "Redmi Note 7 Pro", "violet"),
    ("Xiaomi", "Redmi Note 10 Pro / Pro Max", "sweet"),
    ("Xiaomi", "Redmi Note 10S / 10S NFC", "rosemary"),
    ("Xiaomi", "POCO M5s", "rosemary"),
    ("Xiaomi", "Redmi Note 13 Pro 5G", "garnet"),
    ("Xiaomi", "POCO X6 5G", "garnet"),
    ("Xiaomi", "Redmi Note 8 / 8T", "ginkgo"),
    ("Nothing", "Phone (1)", "Spacewar"),
    ("Nothing", "Phone (2)", "Pong"),
    ("Asus", "ASUS Zenfone 5Z (ZS620KL)", "Z01R"),
    ("Asus", "ZenFone 8", "sake"),
    ("Motorola", "defy 2021", "bathena"),
    ("Motorola", "edge 20", "berlin"),
    ("Motorola", "edge 20 pro", "pstar"),
    ("Motorola", "edge 2024", "avatrn"),
    ("Motorola", "edge 30", "dubai"),
    ("Motorola", "edge 30 fusion", "tundra"),
    ("Motorola", "edge 30 neo", "miami"),
    ("Motorola", "edge 30 ultra", "eqs"),
    ("Motorola", "edge 40 pro / X40", "rtwo"),
    ("Motorola", "edge+ (2023)", "rtwo"),
    ("Motorola", "edge s / moto g100", "nio"),
    ("Motorola", "moto e7 plus / K12", "guam"),
    ("Motorola", "moto g 5G (2024)", "fogo"),
    ("Motorola", "g10 / g10 power / K13 Note", "capri"),
    ("Motorola", "moto g power (2021)", "borneo"),
    ("Motorola", "moto g stylus 5G", "denver"),
    ("Motorola", "moto g stylus 5G (2022)", "milanf"),
    ("Motorola", "moto g200 5G / Edge S30", "xpeng"),
    ("Motorola", "moto g30 / K13 Pro", "caprip"),
    ("Motorola", "moto g32", "devon"),
    ("Motorola", "g34 5G / g45 5G", "fogos"),
    ("Motorola", "moto g42", "hawao"),
    ("Motorola", "moto g52", "rhode"),
    ("Motorola", "moto g6 plus", "evert"),
    ("Motorola", "moto g7", "river"),
    ("Motorola", "moto g7 play", "channel"),
    ("Motorola", "moto g7 plus", "lake"),
    ("Motorola", "moto g7 power", "ocean"),
    ("Motorola", "moto g9 / g9 play / K12 Note", "guamp"),
    ("Motorola", "moto g9 power / K12 Pro", "cebu"),
    ("Motorola", "moto g 5G / one 5G ace", "kiev"),
    ("Motorola", "moto g 5G plus / one 5G", "nario"),
    ("Motorola", "moto g82 5G", "rhodep"),
    ("Motorola", "moto g84 5G", "bangkk"),
    ("Motorola", "x4", "payton"),
    ("Motorola", "z2 force / moto z (2018)", "nash"),
    ("Motorola", "z3", "messi"),
    ("Motorola", "z3 play", "beckham"),
    ("Motorola", "one action", "troika"),
    ("Motorola", "one vision / p50", "kane"),
    ("Motorola", "ThinkPhone by motorola", "bronco"),
    ("Nokia", "Nokia 6.1 (2018)", "PL2"),
    ("Nokia", "Nokia 7 plus", "B2N"),
    ("Nokia", "Nokia 8", "NB1"),
    ("Realme", "Realme 9 5G / Q5", "oscar"),
    ("Realme", "Realme Note 9 Pro 5G", "oscar"),
    ("Realme", "Realme 10 Pro 5G", "luigi"),
    ("SHIFT", "SHIFT6mq", "axolotl"),
    ("Essential", "PH-1", "mata"),
    ("F(x)tec", "Pro¹", "pro1"),
    ("F(x)tec", "Pro¹ X", "pro1x"),
    ("Lenovo", "Z5 Pro GT", "heart"),
    ("Lenovo", "Z6 Pro", "zippo"),
    ("LG", "ThinQ (G710N)", "g710n"),
    ("LG", "G7 ThinQ (G710ULM/VMX)", "g710ulm"),
    ("LG", "Style3", "style3lm"),
    ("LG", "V30 (Japan)", "l01k"),
    ("LG", "V35 ThinQ", "judyp"),
    ("LG", "V40 ThinQ", "judypn"),
    ("LG", "V60 ThinQ", "timelm"),
    ("LG", "Velvet", "caymanslm"),
    ("Nubia", "Mini 5G", "TP1803"),
    ("Nubia", "Red Magic Mars", "nx619j"),
    ("Nubia", "Red Magic Mars", "nx619j"),
    ("Nubia", "Red Magic Mars", "nx619j"),
    ("Nubia", "Red Magic 5G / 5S", "nx659j"),
    ("Nubia", "X", "nx616j"),
    ("Nubia", "Z17", "nx563j"),
    ("Nubia", "Z18", "nx606j"),
    ("Razer", "Edge WiFi / Edge 5G", "nicole"),
    ("Razer", "Phone", "cheryl"),
    ("Razer", "Phone 2", "aura"),
    ("Solana", "Saga", "ingot"),
    ("Sony", "Xperia 1 II", "pdx203"),
    ("Sony", "Xperia 1 III", "pdx215"),
    ("Sony", "Xperia 1 V", "pdx234"),
    ("Sony", "Xperia 10", "kirin"),
    ("Sony", "Xperia 10 IV", "pdx225"),
    ("Sony", "Xperia 10 Plus", "mermaid"),
    ("Sony", "Xperia 10 V", "pdx235"),
    ("Sony", "Xperia 5 II", "pdx206"),
    ("Sony", "Xperia 5 III", "pdx214"),
    ("Sony", "Xperia 5 V", "pdx237"),
    ("Sony", "Xperia XA2", "pioneer"),
    ("Sony", "Xperia XA2 Plus", "voyager"),
    ("Sony", "Xperia XA2 Ultra", "discovery"),
    ("Sony", "Xperia XZ2", "akari"),
    ("Sony", "Xperia XZ2 Compact", "xz2c"),
    ("Sony", "Xperia XZ2 Premium", "aurora"),
    ("Sony", "Xperia XZ3", "akatsuki"),
    ("ZTE", "Axon 9 Pro", "akershus"),
    ("Walmart", "onn. TV Box 4K (2021)", "dopinder"),
    ("NVIDIA", "Shield TV (2019) [Android TV]", "sif"),
    ("Dynalink", "TV Box 4K (2021)", "wade"),
    ("Fairphone", "Fairphone 3 / 3+", "FP3"),
    ("Fairphone", "Fairphone 4", "FP4"),
    ("Fairphone", "Fairphone 5", "FP5"),
)

BRANDS = [
    "Google",
    "Samsung",
    "OnePlus",
    "Xiaomi",
    "Nothing",
    "Sony",
    "Asus",
    "Motorola",
    "Nokia",
    "SHIFT",
    "Realme",
    "Lenovo",
    "LG",
    "Nubia",
    "Razer",
    "Fairphone",
    "Essential",
    "Solana",
    "Walmart",
    "NVIDIA",
    "Dynalink",
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
