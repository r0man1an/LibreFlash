from __future__ import annotations

from pathlib import Path
import threading
import subprocess
import shutil
from typing import Optional

import FreeSimpleGUI as sg

from logic import (
    BRANDS,
    CODENAME_BY_BRAND_MODEL,
    DownloadCallbacks,
    DownloadProgress,
    classify_flash_image,
    adb_reboot_fastboot,
    adb_reboot_recovery,
    adb_reboot_system,
    adb_reboot_download,
    adb_connected_codename,
    download_with_progress,
    get_suggestions,
    latest_nightly,
    latest_recovery_or_boot_for_device,
    latest_vbmeta_via_mirrorbits,
    latest_magisk_apk,
    archive_devices,
    latest_archive_build,
)

sg.theme("DarkGrey13")

TITLE_FONT = ("Helvetica", 22, "bold")
DESC_FONT = ("Helvetica", 11)
BUTTON_FONT = ("Helvetica", 14, "bold")
SUBTITLE_FONT = ("Helvetica", 18, "bold")
TAB_FONT = ("Helvetica", 11, "bold")


def check_dependencies_or_exit() -> None:
    required = ["adb", "fastboot", "pkexec"]
    optional = ["heimdall"]
    missing_req = [x for x in required if shutil.which(x) is None]
    missing_opt = [x for x in optional if shutil.which(x) is None]

    if missing_req:
        sg.popup(
            "Missing required tools:\n"
            f"  {', '.join(missing_req)}\n\n"
            "Install Android platform-tools (adb/fastboot).\n\n"
            "Debian/Ubuntu:\n  sudo apt install android-tools-adb android-tools-fastboot\n"
            "Arch:\n  sudo pacman -S android-tools\n"
            "Fedora:\n  sudo dnf install android-tools\n",
            title="Missing dependencies",
        )

        raise SystemExit(1)

    if missing_opt:
        sg.popup(
            "Optional tool missing:\n"
            f"  {', '.join(missing_opt)}\n\n"
            "Samsung flashing requires Heimdall.\n"
            "Debian/Ubuntu:\n  sudo apt install heimdall-flash\n"
            "Arch:\n  sudo pacman -S heimdall\n"
            "Fedora:\n  sudo dnf install heimdall\n",
            title="Optional dependency missing",
        )


def safe_connected_codename() -> str:
                                                                       
    try:
        return (adb_connected_codename() or "").strip()
    except Exception:
                                                                              
        return ""


def _format_codename_line(connected: str, selected: str) -> str:
    connected = (connected or "").strip()
    selected = (selected or "").strip()
    parts: list[str] = []
    if connected:
        parts.append(f"Codename (connected): {connected}")
    if selected:
        parts.append(f"Codename (selected): {selected}")
    return "    ".join(parts)


def make_main_view():
    return [
        [sg.VPush()],
        [
            sg.Text(
                "LibreFlash",
                font=TITLE_FONT,
                justification="center",
                expand_x=True,
            )
        ],
        [
            sg.Text(
                "Download and flash custom ROMs safely with an intuitive GUI.",
                font=DESC_FONT,
                justification="center",
                expand_x=True,
                pad=(0, 10),
            )
        ],
        [sg.VPush()],
        [
            sg.Button("⬇  Download", key="-DOWNLOAD-", size=(18, 2), font=BUTTON_FONT),
            sg.Text(" " * 4),
            sg.Button("⚡  Flash", key="-FLASH-", size=(18, 2), font=BUTTON_FONT),
        ],
        [sg.VPush()],
    ]


def make_manual_tab():
    desc_line = [
        sg.Text(
            "Manual: select brand, type model, pick suggestion, then press a download button.",
            justification="center",
            expand_x=True,
            pad=(0, 10),
        )
    ]

    brand_w = 18

    model_w = 24

    gap_x = 42

    selector_grid = sg.Column(
        [
            [
                sg.Text(
                    "Brand",
                    justification="center",
                    size=(brand_w, 1),
                    pad=((0, gap_x), 2),
                ),
                sg.Text("Model", justification="center", size=(model_w, 1), pad=(0, 2)),
            ],
            [
                sg.Combo(
                    BRANDS,
                    default_value="Google",
                    key="-BRAND-",
                    readonly=True,
                    enable_events=True,
                    size=(brand_w, 1),
                    pad=((0, gap_x), 0),
                ),
                sg.Input(
                    key="-MODEL-", enable_events=True, size=(model_w, 1), pad=(0, 0)
                ),
            ],
            [
                sg.Text("", size=(brand_w, 3), pad=((0, gap_x), 0)),
                sg.Listbox(
                    values=[],
                    key="-SUGGEST-",
                    size=(model_w, 5),
                    enable_events=True,
                    pad=(0, 0),
                ),
            ],
        ],
        element_justification="center",
        pad=(10, 0),
    )

    top_row = sg.Column(
        [[selector_grid]], element_justification="center", expand_x=True
    )

    codename_row = sg.Column(
        [
            [
                sg.Text(
                    "",
                    key="-CODENAME_LINE-",
                    pad=(0, 0),
                    font=("Helvetica", 11, "bold"),
                    justification="center",
                    expand_x=True,
                ),
                                                                                         
                sg.Text("", key="-CODENAME_TXT-", visible=False, pad=(0, 0)),
            ]
        ],
        element_justification="center",
        expand_x=True,
        pad=(0, 8),
    )

    buttons_row = sg.Column(
        [
            [
                sg.Button("Download ROM", key="-DL_ROM-", size=(14, 1)),
                sg.Button("Download Recovery", key="-DL_RECOVERY-", size=(18, 1)),
                sg.Button(
                    "Download VBMETA", key="-DL_VBMETA-", size=(16, 1), visible=False
                ),
                sg.Button("Back", key="-BACK-", size=(10, 1)),
            ]
        ],
        element_justification="center",
        expand_x=True,
        pad=(0, 6),
    )

    progress_row = sg.Column(
        [
            [
                sg.ProgressBar(
                    max_value=100,
                    orientation="h",
                    size=(34, 12),
                    key="-PROG-",
                    visible=False,
                ),
                sg.Button("Cancel", key="-CANCEL_DL-", size=(10, 1), visible=False),
            ]
        ],
        element_justification="center",
        expand_x=True,
        pad=(0, 6),
    )

    return [
        [sg.VPush()],
        desc_line,
        [top_row],
        [sg.VPush()],
        [codename_row],
        [buttons_row],
        [progress_row],
        [sg.VPush()],
    ]


def make_archive_tab():
    desc_line = [
        sg.Text(
            "Find and download legacy LineageOS builds from the unofficial archive.",
            justification="center",
            expand_x=True,
            pad=(0, 10),
        )
    ]

    model_w = 24

    selector_grid = sg.Column(
        [
            [
                sg.Text(
                    "Device codename",
                    justification="center",
                    size=(model_w, 1),
                    pad=(0, 2),
                ),
            ],
            [
                sg.Input(
                    key="-ARCH_MODEL-",
                    enable_events=True,
                    size=(model_w, 1),
                    pad=(0, 0),
                )
            ],
            [
                sg.Listbox(
                    values=[],
                    key="-ARCH_SUGGEST-",
                    size=(model_w, 5),
                    enable_events=True,
                    pad=(0, 0),
                )
            ],
        ],
        element_justification="center",
        pad=(10, 0),
    )

    top_row = sg.Column(
        [[selector_grid]], element_justification="center", expand_x=True
    )

    selected_row = sg.Column(
        [
            [
                sg.Text(
                    "",
                    key="-ARCH_CODENAME_LINE-",
                    pad=(0, 0),
                    font=("Helvetica", 11, "bold"),
                    justification="center",
                    expand_x=True,
                ),
                                                                                         
                sg.Text("", key="-ARCH_SELECTED_TXT-", visible=False, pad=(0, 0)),
            ]
        ],
        element_justification="center",
        expand_x=True,
        pad=(0, 8),
    )

    buttons_row = sg.Column(
        [
            [
                sg.Button("Download ROM", key="-ARCH_DL_ROM-", size=(14, 1)),
                sg.Button("Refresh", key="-ARCH_REFRESH-", size=(10, 1)),
                sg.Button("Back", key="-ARCH_BACK-", size=(10, 1)),
            ]
        ],
        element_justification="center",
        expand_x=True,
        pad=(0, 6),
    )

    progress_row = sg.Column(
        [
            [
                sg.ProgressBar(
                    max_value=100,
                    orientation="h",
                    size=(34, 12),
                    key="-ARCH_PROG-",
                    visible=False,
                ),
                sg.Button(
                    "Cancel",
                    key="-ARCH_CANCEL_DL-",
                    size=(10, 1),
                    visible=False,
                ),
            ]
        ],
        element_justification="center",
        expand_x=True,
        pad=(0, 6),
    )

    return [
        [sg.VPush()],
        desc_line,
        [top_row],
        [sg.VPush()],
        [selected_row],
        [buttons_row],
        [progress_row],
        [sg.VPush()],
    ]


def make_download_view():
    auto_tab = [
        [sg.VPush()],
        [
            sg.Text(
                "Device recognition coming soon.", justification="center", expand_x=True
            )
        ],
        [sg.VPush()],
    ]

    manual_tab = make_manual_tab()

    archive_tab = make_archive_tab()

    return [
        [sg.VPush()],
        [
            sg.Text(
                "Download", font=SUBTITLE_FONT, justification="center", expand_x=True
            )
        ],
        [
            sg.Text(
                "Choose how you want to download files.",
                justification="center",
                expand_x=True,
                pad=(0, 10),
            )
        ],
        [sg.VPush()],
        [
            sg.TabGroup(
                [
                    [
                        sg.Tab("Manual", manual_tab, font=TAB_FONT),
                        sg.Tab("Auto", auto_tab, font=TAB_FONT),
                        sg.Tab("Unofficial", archive_tab, font=TAB_FONT),
                    ]
                ],
                key="-DL_TABS-",
                enable_events=True,
                expand_x=True,
                expand_y=True,
            )
        ],
        [sg.VPush()],
    ]


def make_fastboot_tab():
    left = sg.Column(
        [
            [
                sg.Text(
                    "Use this method for most Android phones (except Samsung).",
                    justification="left",
                )
            ],
            [
                sg.Text(
                    "Before you flash:",
                    font=("Helvetica", 12, "bold"),
                    justification="left",
                )
            ],
            [
                sg.Text(
                    "• Bootloader MUST be unlocked\n"
                    "• Device must be in Fastboot mode\n"
                    "• Use files made for YOUR device only\n"
                    "• Flash recovery BEFORE flashing a ROM (TWRP also supported)\n"
                    "• First boot can take several minutes",
                    justification="left",
                )
            ],
            [sg.VPush()],
        ],
        expand_x=True,
        expand_y=True,
    )

    right = sg.Column(
        [
            [sg.Text("Reboot", font=("Helvetica", 12, "bold"))],
            [
                sg.Combo(
                    [
                        "Reboot to fastboot (adb)",
                        "Reboot to recovery (adb)",
                        "Reboot device (adb)",
                        "Reboot to system (fastboot)",
                    ],
                    default_value="Reboot to fastboot (adb)",
                    key="-FB_REBOOT_ACTION-",
                    readonly=True,
                    size=(26, 1),
                )
            ],
            [sg.Button("Go", key="-FB_REBOOT_GO-", size=(8, 1))],
        ],
        element_justification="center",
        pad=(10, 0),
    )

    flash_actions = sg.Column(
        [
            [
                sg.Button("Flash recovery", key="-FB_FLASH_RECOVERY-", size=(16, 1)),
                sg.Button("Flash ROM zip", key="-FB_FLASH_ROM-", size=(16, 1)),
            ]
        ],
        element_justification="center",
        expand_x=True,
        pad=(0, 12),
    )

    return [
        [sg.Column([[left, right]], expand_x=True, expand_y=True)],
        [flash_actions],
    ]


def make_heimdall_tab():
    left = sg.Column(
        [
            [sg.Text("Use this method for all Samsung devices.", justification="left")],
            [
                sg.Text(
                    "Before you flash:",
                    font=("Helvetica", 12, "bold"),
                    justification="left",
                )
            ],
            [
                sg.Text(
                    "• Bootloader MUST be unlocked\n"
                    "• Device must be in Download mode\n"
                    "• Use files made for YOUR device only\n"
                    "• Flash recovery BEFORE flashing a ROM (TWRP also supported)\n"
                    "• First boot can take several minutes",
                    justification="left",
                )
            ],
            [sg.VPush()],
        ],
        expand_x=True,
        expand_y=True,
    )

    right = sg.Column(
        [
            [sg.Text("Reboot", font=("Helvetica", 12, "bold"))],
            [
                sg.Combo(
                    [
                        "Reboot to download (adb)",
                        "Reboot to recovery (adb)",
                        "Reboot device (adb)",
                    ],
                    default_value="Reboot to download (adb)",
                    key="-HD_REBOOT_ACTION-",
                    readonly=True,
                    size=(26, 1),
                )
            ],
            [sg.Button("Go", key="-HD_REBOOT_GO-", size=(8, 1))],
        ],
        element_justification="center",
        pad=(10, 0),
    )

    flash_actions = sg.Column(
        [
            [
                sg.Button("Flash VBMETA", key="-HD_FLASH_VBMETA-", size=(16, 1)),
                sg.Button("Flash recovery", key="-HD_FLASH_RECOVERY-", size=(16, 1)),
                sg.Button("Flash ROM zip", key="-HD_FLASH_ROM-", size=(16, 1)),
            ]
        ],
        element_justification="center",
        expand_x=True,
        pad=(0, 12),
    )

    return [
        [sg.Column([[left, right]], expand_x=True, expand_y=True)],
        [flash_actions],
    ]


def make_bootloader_tab():
    left = sg.Column(
        [
            [sg.Text("Bootloader", font=("Helvetica", 12, "bold"))],
            [
                sg.Text(
                    "The bootloader controls what software your device is allowed to start.\n"
                    "From here, you can check whether it is locked or unlocked, and change its state.\n"
                    "An unlocked bootloader is required for flashing custom recoveries and ROMs."
                )
            ],
            [
                sg.Text(
                    "Lock the bootloader only when running verified, stock-compatible software.\n"
                    "Samsung devices use a different bootloader system.\n"
                    "So these tools apply to non-Samsung devices only."
                )
            ],
            [sg.VPush()],
        ],
        expand_x=True,
        expand_y=True,
    )

    right = sg.Column(
        [
            [sg.Button("BL Status", key="-UTIL_BL_STATUS-", size=(12, 2))],
            [sg.Button("Unlock BL", key="-UTIL_BL_UNLOCK-", size=(12, 2))],
            [sg.Button("Lock BL", key="-UTIL_BL_LOCK-", size=(12, 2))],
        ],
        element_justification="center",
        pad=(10, 0),
    )

    return [[sg.Column([[left, right]], expand_x=True, expand_y=True)]]


def make_magisk_tab():
    left = sg.Column(
        [
            [sg.Text("Magisk", font=("Helvetica", 12, "bold"))],
            [
                sg.Text(
                    "Magisk provides systemless root and modules.\n"
                    "For recovery-based installs, Magisk can be sideloaded.\n"
                )
            ],
            [sg.Text("Before you flash:", font=("Helvetica", 12, "bold"))],
            [
                sg.Text(
                    "• Device must be in Recovery\n"
                    "• In Recovery, enable 'ADB sideload'\n"
                    "• Click Download to get the latest Magisk APK\n"
                    "• After Downloading, click Flash Magisk and select the Downloaded file\n"
                    "• If something goes wrong, you may need to restore a boot image backup",
                    justification="left",
                )
            ],
            [sg.VPush()],
        ],
        expand_x=True,
        expand_y=True,
    )

    right = sg.Column(
        [
            [sg.Button("Download Magisk", key="-MG_DL-", size=(22, 2))],
            [sg.Button("Flash Magisk", key="-MG_FLASH-", size=(22, 2))],
        ],
        element_justification="center",
        pad=(10, 0),
    )

    return [[sg.Column([[left, right]], expand_x=True, expand_y=True)]]


def make_flash_view():
    tabs = sg.TabGroup(
        [
            [
                sg.Tab("Fastboot (most devices)", make_fastboot_tab(), font=TAB_FONT),
                sg.Tab("Heimdall (Samsung-only)", make_heimdall_tab(), font=TAB_FONT),
                sg.Tab("Bootloader", make_bootloader_tab(), font=TAB_FONT),
                sg.Tab("Magisk", make_magisk_tab(), font=TAB_FONT),
            ]
        ],
        expand_x=True,
        expand_y=True,
    )

    return [
        [sg.VPush()],
        [
            sg.Text(
                "Flashing", font=SUBTITLE_FONT, justification="center", expand_x=True
            )
        ],
        [
            sg.Text(
                "Choose a flashing method.",
                justification="center",
                expand_x=True,
                pad=(0, 10),
            )
        ],
        [tabs],
        [sg.VPush()],
        [sg.Button("Back", key="-FLASH_BACK-", size=(10, 1))],
    ]


def refresh_manual(window: sg.Window):
    brand = window["-BRAND-"].get() or "Google"
    typed = window["-MODEL-"].get() or ""
    window["-SUGGEST-"].update(values=get_suggestions(brand, typed))
    window["-DL_VBMETA-"].update(visible=(brand == "Samsung"))


def clear_manual(window: sg.Window):
    window["-MODEL-"].update("")

    window["-CODENAME_TXT-"].update("")

    connected = safe_connected_codename()
    window["-CODENAME_LINE-"].update(_format_codename_line(connected, ""))

    refresh_manual(window)


def clear_archive(window: sg.Window):
                                                                                     
    window["-ARCH_MODEL-"].update("")
    window["-ARCH_SELECTED_TXT-"].update("")
                                                        
    connected = safe_connected_codename()
    window["-ARCH_CODENAME_LINE-"].update(_format_codename_line(connected, ""))



def set_dl_ui(window: sg.Window, active: bool):
    window["-DL_ROM-"].update(disabled=active)
    window["-DL_VBMETA-"].update(disabled=active)
    window["-DL_RECOVERY-"].update(disabled=active)
    window["-MG_DL-"].update(disabled=active)
    window["-ARCH_DL_ROM-"].update(disabled=active)
    window["-ARCH_REFRESH-"].update(disabled=active)
    window["-ARCH_BACK-"].update(disabled=active)
    window["-ARCH_MODEL-"].update(disabled=active)
    window["-ARCH_SUGGEST-"].update(disabled=active)
    window["-BACK-"].update(disabled=active)
    window["-BRAND-"].update(disabled=active)
    window["-MODEL-"].update(disabled=active)
    window["-SUGGEST-"].update(disabled=active)
    window["-PROG-"].update(visible=active)
    window["-ARCH_PROG-"].update(visible=active)
    window["-CANCEL_DL-"].update(visible=active)
    window["-ARCH_CANCEL_DL-"].update(visible=active)

    if not active:
        window["-PROG-"].update(current_count=0, max=100)
        window["-ARCH_PROG-"].update(current_count=0, max=100)


def run_live_cmd(title: str, cmd: list[str]) -> tuple[int, list[str]]:
    out_layout = [
        [sg.Text(title, font=("Helvetica", 12, "bold"))],
        [sg.Multiline("", key="-OUT-", size=(90, 25), autoscroll=True, disabled=True)],
        [sg.Button("Close", key="-OUT_CLOSE-", disabled=True)],
    ]

    out_win = sg.Window(title, out_layout, modal=True, finalize=True)

    lines: list[str] = []

    def worker():
        try:
            p = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            assert p.stdout is not None

            for line in p.stdout:
                out_win.write_event_value("-CMD_LINE-", line.rstrip("\n"))

            rc = p.wait()

            out_win.write_event_value("-CMD_DONE-", {"rc": rc})

        except Exception as e:
            out_win.write_event_value("-CMD_DONE-", {"rc": 999, "err": str(e)})

    threading.Thread(target=worker, daemon=True).start()

    rc = 999

    while True:
        e, v = out_win.read()

        if e == "-CMD_LINE-":
            s = v["-CMD_LINE-"]

            lines.append(s)

            out_win["-OUT-"].update(s + "\\n", append=True)

        if e == "-CMD_DONE-":
            data = v["-CMD_DONE-"]

            rc = int(data.get("rc", 999))

            err = data.get("err")

            if err:
                lines.append(f"ERROR: {err}")

                out_win["-OUT-"].update(f"\\nERROR: {err}\\n", append=True)

            out_win["-OUT-"].update(f"\\n\\n--- DONE (rc={rc}) ---\\n", append=True)

            out_win["-OUT_CLOSE-"].update(disabled=False)

        if e in (sg.WINDOW_CLOSED, "-OUT_CLOSE-"):
            break

    out_win.close()

    return rc, lines


def _file_action_dialog(
    *,
    title: str,
    instructions: str,
    file_types: list[tuple[str, str]],
    initial_folder: Path,
    start_label: str = "Start",
) -> Optional[str]:
                                                                               
    layout = [
        [sg.Text(title, font=("Helvetica", 14, "bold"), justification="center", expand_x=True)],
        [sg.Text(instructions, justification="left", expand_x=True)],
        [sg.Text("Selected file:")],
        [sg.Input("", key="-FILE-", expand_x=True, readonly=True), sg.Button("Choose…", key="-CHOOSE-")],
        [sg.VPush()],
        [sg.Button(start_label, key="-START-", size=(10, 1)), sg.Button("Cancel", key="-CANCEL-", size=(10, 1))],
    ]
    win = sg.Window(title, layout, modal=True, finalize=True)
    picked = ""
    while True:
        ev, _ = win.read()
        if ev in (sg.WINDOW_CLOSED, "-CANCEL-"):
            win.close()
            return None
        if ev == "-CHOOSE-":
            p = sg.popup_get_file(
                "Choose file",
                no_window=True,
                initial_folder=str(initial_folder),
                file_types=tuple(file_types),
            )
            if p:
                picked = p
                win["-FILE-"].update(picked)
        if ev == "-START-":
            win.close()
            return picked or None


def sideload_dialog(initial: Path) -> Optional[str]:
    return _file_action_dialog(
        title="ADB sideload",
        instructions="Choose a ZIP to sideload via recovery.",
        file_types=[("ZIP files", "*.zip"), ("All files", "*.*")],
        initial_folder=initial,
        start_label="Sideload",
    )

def magisk_sideload_dialog(initial: Path) -> Optional[str]:
    return _file_action_dialog(
        title="Magisk (ZIP) sideload",
        instructions="Choose a Magisk ZIP to sideload via recovery.",
        file_types=[("ZIP files", "*.zip"), ("All files", "*.*")],
        initial_folder=initial,
        start_label="Sideload",
    )

def flash_dialog(initial: Path) -> Optional[str]:
    return _file_action_dialog(
        title="Flash image",
        instructions="Choose a recovery.img or boot.img to flash.",
        file_types=[("Image files", "*.img"), ("All files", "*.*")],
        initial_folder=initial,
        start_label="Flash",
    )

def _do_fb_reboot(choice: str):
    if choice == "Reboot device (adb)":
        rc, last, _lines = adb_reboot_system()

        sg.popup(f"{choice}\n\nReturn code: {rc}\n{last}")

        return

    if choice == "Reboot to recovery (adb)":
        rc, last, _lines = adb_reboot_recovery()

        sg.popup(f"{choice}\n\nReturn code: {rc}\n{last}")

        return

    if choice == "Reboot to fastboot (adb)":
        rc, last, _lines = adb_reboot_fastboot()

        sg.popup(f"{choice}\n\nReturn code: {rc}\n{last}")

        return

    if choice == "Reboot to system (fastboot)":
        rc, _lines = run_live_cmd("Fastboot reboot", ["pkexec", "fastboot", "reboot"])

        if rc == 0:
            sg.popup("Fastboot reboot OK.", title="Done")

        else:
            sg.popup(f"Fastboot reboot failed (rc={rc}).", title="Error")

        return

    sg.popup(f"Unknown action: {choice}", title="Error")


def _do_hd_reboot(choice: str):
    if choice == "Reboot device (adb)":
        rc, last, _lines = adb_reboot_system()

        sg.popup(f"{choice}\n\nReturn code: {rc}\n{last}")

        return

    if choice == "Reboot to recovery (adb)":
        rc, last, _lines = adb_reboot_recovery()

        sg.popup(f"{choice}\n\nReturn code: {rc}\n{last}")

        return

    if choice == "Reboot to download (adb)":
        rc, last, _lines = adb_reboot_download()

        sg.popup(f"{choice}\n\nReturn code: {rc}\n{last}")

        return

    sg.popup(f"Unknown action: {choice}", title="Error")


def main():
    check_dependencies_or_exit()

    main_col = sg.Column(
        make_main_view(),
        key="-PAGE_MAIN-",
        visible=True,
        expand_x=True,
        expand_y=True,
        element_justification="center",
    )

    download_col = sg.Column(
        make_download_view(),
        key="-PAGE_DOWNLOAD-",
        visible=False,
        expand_x=True,
        expand_y=True,
        element_justification="center",
    )

    flash_col = sg.Column(
        make_flash_view(),
        key="-PAGE_FLASH-",
        visible=False,
        expand_x=True,
        expand_y=True,
        element_justification="center",
    )

    window = sg.Window(
        "LibreFlash alpha-4",
        [[main_col, download_col, flash_col]],
        size=(700, 460),
        element_justification="center",
        finalize=True,
    )

    refresh_manual(window)

    ARCH_ALL_DEVICES: list[str] = []

    def refresh_archive(window: sg.Window):
        nonlocal ARCH_ALL_DEVICES
        try:
            devs = archive_devices()
            ARCH_ALL_DEVICES = devs
            window["-ARCH_SUGGEST-"].update(values=devs[:200])
            window["-ARCH_MODEL-"].update("")
            window["-ARCH_SELECTED_TXT-"].update("")
            connected = safe_connected_codename()
            window["-ARCH_CODENAME_LINE-"].update(_format_codename_line(connected, ""))
        except Exception as e:
            sg.popup(f"Failed to load archive devices.\n\n{e}")

    def refresh_archive_suggestions(window: sg.Window):
        typed = (window["-ARCH_MODEL-"].get() or "").strip().lower()
        if not ARCH_ALL_DEVICES:
            window["-ARCH_SUGGEST-"].update(values=[])
            return
        if not typed:
            window["-ARCH_SUGGEST-"].update(values=ARCH_ALL_DEVICES[:200])
            return
        window["-ARCH_SUGGEST-"].update(
            values=[d for d in ARCH_ALL_DEVICES if typed in d.lower()][:15]
        )

    refresh_archive(window)

                                                                       
    dev_stop = threading.Event()
    last_seen = {"codename": None}

    def dev_poller():
        import time as _t
        while not dev_stop.is_set():
            c = safe_connected_codename()
            if c != last_seen["codename"]:
                last_seen["codename"] = c
                window.write_event_value("-DEV_CODENAME-", {"codename": c})
            _t.sleep(1.0)

    threading.Thread(target=dev_poller, daemon=True).start()


    dl_stop = threading.Event()

    dl_active = False

    def start_download(kind: str, url: str, default_filename: str):
        nonlocal dl_active

        downloads_dir = Path.home() / "Downloads"

        downloads_dir.mkdir(exist_ok=True)

        save_to = sg.popup_get_file(
            f"Save {kind}",
            save_as=True,
            no_window=True,
            initial_folder=str(downloads_dir),
            default_path=default_filename,
        )

        if not save_to:
            return

        out_path = Path(save_to)

        dl_stop.clear()

        dl_active = True

        set_dl_ui(window, True)

        def on_progress(p: DownloadProgress):
            window.write_event_value(
                "-DL_PROGRESS-", {"done": p.done, "total": p.total}
            )

        def on_done(path: Path):
            window.write_event_value("-DL_DONE-", {"path": str(path)})

        def on_error(msg: str):
            window.write_event_value("-DL_ERROR-", {"error": msg})

        def on_cancelled():
            window.write_event_value("-DL_CANCELLED-", {})

        cb = DownloadCallbacks(
            on_progress=on_progress,
            on_done=on_done,
            on_error=on_error,
            on_cancelled=on_cancelled,
        )

        def worker():
            download_with_progress(url, out_path, stop_event=dl_stop, cb=cb)

        threading.Thread(target=worker, daemon=True).start()

    def selected_brand_model_codename(values) -> tuple[str, str, str]:
        brand = values["-BRAND-"]

        model = (values["-MODEL-"] or "").strip()

        codename = CODENAME_BY_BRAND_MODEL.get((brand, model), "")

        return brand, model, codename

    while True:
        event, values = window.read()

        if event == sg.WINDOW_CLOSED:
            break

        if event == "-DEV_CODENAME-":
            c = (values.get("-DEV_CODENAME-", {}) or {}).get("codename", "")
                                                     
            selected_official = (values.get("-CODENAME_TXT-") or "").strip()
            window["-CODENAME_LINE-"].update(_format_codename_line(c, selected_official))
                                          
            selected_arch = (values.get("-ARCH_SELECTED_TXT-") or "").strip()
            window["-ARCH_CODENAME_LINE-"].update(_format_codename_line(c, selected_arch))


        if event == "-FB_FLASH_RECOVERY-":
            flash_dialog("Fastboot", "Flash recovery")

        elif event == "-FB_FLASH_ROM-":
            sideload_dialog("ADB Sideload ROM zip")

        elif event == "-HD_FLASH_VBMETA-":
            vbmeta_flash_dialog()

        elif event == "-HD_FLASH_RECOVERY-":
            flash_dialog("Heimdall", "Flash recovery")

        elif event == "-HD_FLASH_ROM-":
            sideload_dialog("ADB Sideload ROM zip")

        elif event == "-MG_DL-":
            try:
                m = latest_magisk_apk()

                start_download("Magisk APK", m["url"], m["filename"])

            except Exception as e:
                sg.popup(f"Magisk download setup failed.\n\n{e}")

        elif event == "-MG_FLASH-":
            magisk_sideload_dialog("ADB Sideload Magisk")

        elif event == "-FB_REBOOT_GO-":
            _do_fb_reboot(values.get("-FB_REBOOT_ACTION-", "Reboot device (adb)"))

        elif event == "-HD_REBOOT_GO-":
            _do_hd_reboot(values.get("-HD_REBOOT_ACTION-", "Reboot device (adb)"))

        elif event == "-DOWNLOAD-":
            window["-PAGE_MAIN-"].update(visible=False)

            window["-PAGE_DOWNLOAD-"].update(visible=True)

            window["-PAGE_FLASH-"].update(visible=False)

            clear_manual(window)
            clear_archive(window)

        elif event == "-FLASH-":
            window["-PAGE_MAIN-"].update(visible=False)

            window["-PAGE_DOWNLOAD-"].update(visible=False)

            window["-PAGE_FLASH-"].update(visible=True)

        elif event == "-FLASH_BACK-":
            window["-PAGE_FLASH-"].update(visible=False)

            window["-PAGE_MAIN-"].update(visible=True)

        elif event == "-ARCH_BACK-":
            if dl_active:
                sg.popup("Download is running. Cancel it first.")
            else:
                window["-PAGE_DOWNLOAD-"].update(visible=False)
                window["-PAGE_MAIN-"].update(visible=True)

        elif event == "-BACK-":
            if dl_active:
                sg.popup("Download is running. Cancel it first.")

            else:
                window["-PAGE_DOWNLOAD-"].update(visible=False)

                window["-PAGE_MAIN-"].update(visible=True)

        elif event == "-BRAND-":
            window["-MODEL-"].update("")

            window["-CODENAME_TXT-"].update("")

            connected = safe_connected_codename()
            window["-CODENAME_LINE-"].update(_format_codename_line(connected, ""))

            refresh_manual(window)

        elif event == "-MODEL-":
            refresh_manual(window)

            brand = values["-BRAND-"]

            model_exact = (values["-MODEL-"] or "").strip()

            window["-CODENAME_TXT-"].update(
                CODENAME_BY_BRAND_MODEL.get((brand, model_exact), "")
            )

            selected = CODENAME_BY_BRAND_MODEL.get((brand, model_exact), "")
            connected = safe_connected_codename()
            window["-CODENAME_LINE-"].update(_format_codename_line(connected, selected))

        elif event == "-SUGGEST-":
            brand = values["-BRAND-"]

            picked = (values["-SUGGEST-"] or [None])[0]

            if picked:
                window["-MODEL-"].update(picked)

                window["-CODENAME_TXT-"].update(
                    CODENAME_BY_BRAND_MODEL.get((brand, picked), "")
                )

                selected = CODENAME_BY_BRAND_MODEL.get((brand, picked), "")
                connected = safe_connected_codename()
                window["-CODENAME_LINE-"].update(
                    _format_codename_line(connected, selected)
                )


        elif event == "-DL_TABS-":
                                                                                               
            current_tab = values.get("-DL_TABS-")
            if isinstance(current_tab, str) and current_tab.lower().startswith("unofficial"):
                connected = safe_connected_codename()
                selected = (values.get("-ARCH_SELECTED_TXT-") or "").strip()
                window["-ARCH_CODENAME_LINE-"].update(_format_codename_line(connected, selected))
            else:
                connected = safe_connected_codename()
                selected = (values.get("-CODENAME_TXT-") or "").strip()
                window["-CODENAME_LINE-"].update(_format_codename_line(connected, selected))

        elif event == "-ARCH_REFRESH-":
            refresh_archive(window)

        elif event == "-ARCH_MODEL-":
            refresh_archive_suggestions(window)
            picked_exact = (values.get("-ARCH_MODEL-") or "").strip()
            window["-ARCH_SELECTED_TXT-"].update(
                picked_exact if picked_exact in ARCH_ALL_DEVICES else ""
            )
            selected = picked_exact if picked_exact in ARCH_ALL_DEVICES else ""
            connected = safe_connected_codename()
            window["-ARCH_CODENAME_LINE-"].update(
                _format_codename_line(connected, selected)
            )

        elif event == "-ARCH_SUGGEST-":
            picked = (values.get("-ARCH_SUGGEST-") or [None])[0]
            if picked:
                window["-ARCH_MODEL-"].update(picked)
                window["-ARCH_SELECTED_TXT-"].update(picked)
                selected = picked
                connected = safe_connected_codename()
                window["-ARCH_CODENAME_LINE-"].update(
                    _format_codename_line(connected, selected)
                )

        elif event == "-ARCH_DL_ROM-":
            device = (values.get("-ARCH_MODEL-") or "").strip()
            if not device:
                sg.popup("Please type or select a device codename first.")
            else:
                try:
                    b = latest_archive_build(device)
                    start_download("Archive ROM ZIP", b["url"], b["filename"])
                except Exception as e:
                    sg.popup(f"Archive ROM download setup failed.\n\n{e}")

        elif event == "-ARCH_CANCEL_DL-":
            if dl_active:
                dl_stop.set()

        elif event == "-DL_VBMETA-":
            brand, _model, codename = selected_brand_model_codename(values)

            if brand != "Samsung":
                sg.popup("VBMETA download is available for Samsung only.")

            elif not codename:
                sg.popup("Please select a valid model from the suggestions first.")

            else:
                try:
                    artifact = latest_vbmeta_via_mirrorbits(codename, max_tries=12)

                    fname = artifact["filename"]

                    date = artifact["date"]

                    default_name = f"{codename}-{date}-{fname}"

                    start_download("VBMETA Image", artifact["url"], default_name)

                except Exception as e:
                    sg.popup(f"VBMETA download setup failed.\n\n{e}")

        elif event == "-DL_ROM-":
            _brand, _model, codename = selected_brand_model_codename(values)

            if not codename:
                sg.popup("Please select a valid model from the suggestions first.")

            else:
                try:
                    b = latest_nightly(codename)

                    start_download("ROM ZIP", b["url"], b["filename"])

                except Exception as e:
                    sg.popup(f"ROM download setup failed.\n\n{e}")

        elif event == "-DL_RECOVERY-":
            brand, model, codename = selected_brand_model_codename(values)

            if not codename:
                sg.popup("Please select a valid model from the suggestions first.")

            else:
                is_pixel = brand == "Google" and model.lower().startswith("pixel")

                try:
                    artifact = latest_recovery_or_boot_for_device(
                        is_pixel=is_pixel,
                        codename=codename,
                        max_tries=12,
                    )

                    fname = artifact["filename"]

                    date = artifact["date"]

                    kind = "Boot Image" if fname == "boot.img" else "Recovery Image"

                    default_name = f"{codename}-{date}-{fname}"

                    start_download(kind, artifact["url"], default_name)

                except Exception as e:
                    sg.popup(f"Recovery/boot download setup failed.\n\n{e}")

        elif event == "-CANCEL_DL-":
            if dl_active:
                dl_stop.set()

        elif event == "-DL_PROGRESS-":
            data = values["-DL_PROGRESS-"]

            done = int(data.get("done", 0))

            total = data.get("total", None)

            if total and total > 0:
                pct = int(done * 100 / total)

                window["-PROG-"].update_bar(pct, max=100)
                window["-ARCH_PROG-"].update_bar(pct, max=100)

            else:
                window["-PROG-"].update_bar((done // (1024 * 1024)) % 100, max=100)
                window["-ARCH_PROG-"].update_bar((done // (1024 * 1024)) % 100, max=100)

        elif event == "-DL_DONE-":
            dl_active = False

            set_dl_ui(window, False)

            saved_path = values["-DL_DONE-"]["path"]

            sg.popup(f"Download finished!\n\nSaved to:\n{saved_path}")

        elif event == "-DL_ERROR-":
            dl_active = False

            set_dl_ui(window, False)

            sg.popup(f"Download failed.\n\n{values['-DL_ERROR-']['error']}")

        elif event == "-DL_CANCELLED-":
            dl_active = False

            set_dl_ui(window, False)

            sg.popup("Download cancelled.")

        elif event == "-UTIL_BL_STATUS-":
            rc, lines = run_live_cmd(
                "Bootloader status (fastboot getvar unlocked)",
                ["pkexec", "fastboot", "getvar", "unlocked"],
            )

            out = "\n".join(lines)

            low = out.lower()

            status = "Unknown"

            if "unlocked:" in low:
                if any(
                    x in low for x in ("unlocked: yes", "unlocked: true", "unlocked: 1")
                ):
                    status = "Unlocked"

                elif any(
                    x in low for x in ("unlocked: no", "unlocked: false", "unlocked: 0")
                ):
                    status = "Locked"

            if rc == 0:
                sg.popup(f"Your bootloader status: {status}", title="BL Status")

            else:
                sg.popup(
                    "Could not reliably read bootloader status.\n\n"
                    f"Parsed: {status}\n\n"
                    f"(rc={rc})\n\nRaw output:\n{out}",
                    title="BL Status",
                )

        elif event == "-UTIL_BL_UNLOCK-":
            confirm = sg.popup_yes_no(
                "Unlock bootloader?\n\nThis often wipes ALL data.\n\nProceed?",
                title="Confirm bootloader unlock",
            )

            if confirm == "Yes":
                run_live_cmd(
                    "Bootloader unlock", ["pkexec", "fastboot", "flashing", "unlock"]
                )

        elif event == "-UTIL_BL_LOCK-":
            confirm = sg.popup_yes_no(
                "Lock bootloader?\n\nLocking with modified software can brick the device.\n"
                "Proceed only if it's safe.\n\nProceed?",
                title="Confirm bootloader lock",
            )

            if confirm == "Yes":
                run_live_cmd(
                    "Bootloader lock", ["pkexec", "fastboot", "flashing", "lock"]
                )

    dev_stop.set()

    window.close()


def vbmeta_flash_dialog(initial: Path) -> Optional[str]:
    return _file_action_dialog(
        title="Flash vbmeta image",
        instructions="Choose a vbmeta*.img to flash.",
        file_types=[("Image files", "*.img"), ("All files", "*.*")],
        initial_folder=initial,
        start_label="Flash",
    )


if __name__ == "__main__":
    main()
