#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
except ImportError as exc:
    print("Missing Tk GUI runtime.", file=sys.stderr)
    print("On Arch run: sudo pacman -Syu tk", file=sys.stderr)
    print(f"Import error: {exc}", file=sys.stderr)
    sys.exit(1)

APP_NAME = "Arch Soundpad"
CONFIG_DIR = Path.home() / ".config" / "arch-soundpad"
CONFIG_FILE = CONFIG_DIR / "clips.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
SINK_NAME = "soundpad_sink"
SOURCE_NAME = "soundpad_mic"

try:
    from Xlib import X, XK, display as xdisplay
    from Xlib import error as xerror

    XLIB_AVAILABLE = True
except Exception:
    XLIB_AVAILABLE = False


class GlobalHotkeyManager:
    def __init__(self, on_trigger):
        self.on_trigger = on_trigger
        self.display = None
        self.root = None
        self.running = False
        self.thread = None
        self.bindings = {}
        self.registered = []

    def _modifier_mask(self, mods):
        mask = 0
        if "control" in mods:
            mask |= X.ControlMask
        if "alt" in mods:
            mask |= X.Mod1Mask
        if "shift" in mods:
            mask |= X.ShiftMask
        if "super" in mods:
            mask |= X.Mod4Mask
        return mask

    def _keysym_to_keycode(self, key_name):
        special = {
            "\\": "backslash",
            "/": "slash",
            "-": "minus",
            "=": "equal",
            "`": "grave",
        }
        key_name = special.get(key_name, key_name)
        if key_name.startswith("f") and key_name[1:].isdigit():
            key_name = key_name.upper()
        keysym = XK.string_to_keysym(key_name)
        if not keysym:
            return None
        keycode = self.display.keysym_to_keycode(keysym)
        return keycode or None

    def _hotkey_to_parts(self, hotkey_text):
        raw = hotkey_text.strip()
        if not raw:
            return None
        parts = [p.strip() for p in raw.split("+") if p.strip()]
        if not parts:
            return None
        key_name = parts[-1].lower()
        mods = set()
        for mod in parts[:-1]:
            lower = mod.lower()
            if lower in ("ctrl", "control"):
                mods.add("control")
            elif lower == "alt":
                mods.add("alt")
            elif lower == "shift":
                mods.add("shift")
            elif lower in ("super", "win", "mod4"):
                mods.add("super")
            else:
                return None
        return mods, key_name

    def start(self):
        if not XLIB_AVAILABLE or self.running:
            return False
        try:
            self.display = xdisplay.Display()
            self.root = self.display.screen().root
            self.running = True
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
            return True
        except Exception:
            self.display = None
            self.root = None
            self.running = False
            return False

    def stop(self):
        if not self.running:
            return
        self.running = False
        if self.thread:
            self.thread.join(timeout=0.5)
        self._unregister_all()
        try:
            if self.display:
                self.display.close()
        except Exception:
            pass
        self.display = None
        self.root = None

    def _unregister_all(self):
        if not self.display or not self.root:
            return
        for keycode, mask in self.registered:
            try:
                self.root.ungrab_key(keycode, mask)
            except Exception:
                pass
        self.registered.clear()
        try:
            self.display.flush()
        except Exception:
            pass

    def register(self, hotkey_actions):
        if not self.running or not self.display or not self.root:
            return []
        self._unregister_all()
        self.bindings = {}
        conflicts = []
        for action, hotkey in hotkey_actions:
            parsed = self._hotkey_to_parts(hotkey)
            if not parsed:
                continue
            mods, key_name = parsed
            keycode = self._keysym_to_keycode(key_name)
            if not keycode:
                conflicts.append(hotkey)
                continue
            base_mask = self._modifier_mask(mods)
            any_grab = False
            for extra in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
                mask = base_mask | extra
                try:
                    self.root.grab_key(keycode, mask, True, X.GrabModeAsync, X.GrabModeAsync)
                    self.display.sync()
                    self.registered.append((keycode, mask))
                    any_grab = True
                except xerror.BadAccess:
                    continue
                except Exception:
                    continue
            if any_grab:
                self.bindings[(keycode, base_mask)] = action
            else:
                conflicts.append(hotkey)
        try:
            self.display.flush()
        except Exception:
            pass
        return conflicts

    def _loop(self):
        while self.running and self.display:
            try:
                while self.display.pending_events():
                    event = self.display.next_event()
                    if event.type != X.KeyPress:
                        continue
                    state = event.state & (X.ShiftMask | X.ControlMask | X.Mod1Mask | X.Mod4Mask)
                    action = self.bindings.get((event.detail, state))
                    if action is not None:
                        self.on_trigger(action)
                time.sleep(0.02)
            except Exception:
                time.sleep(0.1)


class AudioRouter:
    def __init__(self):
        self.sink_module_id = None
        self.source_module_id = None
        self.monitor_module_id = None
        self.mic_loop_module_id = None

    def _run(self, cmd):
        return subprocess.run(cmd, capture_output=True, text=True)

    def _module_id_by_name(self, module_name, arg_token=None):
        result = self._run(["pactl", "list", "short", "modules"])
        if result.returncode != 0:
            return None

        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            mod_id, mod_name, mod_args = parts[0], parts[1], parts[2]
            if mod_name != module_name:
                continue
            if arg_token is None:
                return mod_id
            if arg_token in mod_args:
                return mod_id
        return None

    def ensure_virtual_mic(self):
        self.sink_module_id = self._module_id_by_name("module-null-sink", f"sink_name={SINK_NAME}")
        if not self.sink_module_id:
            sink_cmd = [
                "pactl",
                "load-module",
                "module-null-sink",
                f"sink_name={SINK_NAME}",
                "sink_properties=device.description=SoundpadSink",
            ]
            sink_result = self._run(sink_cmd)
            if sink_result.returncode != 0:
                return False, sink_result.stderr.strip() or sink_result.stdout.strip()
            self.sink_module_id = sink_result.stdout.strip()

        self.source_module_id = self._module_id_by_name("module-remap-source", f"source_name={SOURCE_NAME}")
        if not self.source_module_id:
            source_cmd = [
                "pactl",
                "load-module",
                "module-remap-source",
                f"master={SINK_NAME}.monitor",
                f"source_name={SOURCE_NAME}",
                "source_properties=device.description=SoundpadMic",
            ]
            source_result = self._run(source_cmd)
            if source_result.returncode != 0:
                return False, source_result.stderr.strip() or source_result.stdout.strip()
            self.source_module_id = source_result.stdout.strip()

        return True, f"Ready: sink={SINK_NAME}, mic={SOURCE_NAME}"

    def ensure_local_monitor(self):
        self.monitor_module_id = self._module_id_by_name("module-loopback", f"source={SINK_NAME}.monitor")
        if self.monitor_module_id:
            return True, "Local monitor ready"

        monitor_cmd = [
            "pactl",
            "load-module",
            "module-loopback",
            f"source={SINK_NAME}.monitor",
            "sink=@DEFAULT_SINK@",
            "latency_msec=30",
        ]
        monitor_result = self._run(monitor_cmd)
        if monitor_result.returncode != 0:
            return False, monitor_result.stderr.strip() or monitor_result.stdout.strip()
        self.monitor_module_id = monitor_result.stdout.strip()
        return True, "Local monitor enabled"

    def unload_monitor(self):
        if not self.monitor_module_id:
            return True, "Speaker monitor already disabled"
        unload_result = self._run(["pactl", "unload-module", str(self.monitor_module_id)])
        if unload_result.returncode != 0:
            return False, unload_result.stderr.strip() or unload_result.stdout.strip()
        self.monitor_module_id = None
        return True, "Speaker monitor muted"

    def set_mic_mute(self, muted):
        mute_value = "1" if muted else "0"
        result = self._run(["pactl", "set-source-mute", SOURCE_NAME, mute_value])
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip()
        return True, "Mic muted" if muted else "Mic unmuted"

    def list_input_sources(self):
        result = self._run(["pactl", "list", "short", "sources"])
        if result.returncode != 0:
            return []
        names = []
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            source_name = parts[1].strip()
            if source_name == SOURCE_NAME or source_name.endswith(".monitor"):
                continue
            names.append(source_name)
        return names

    def get_default_source(self):
        result = self._run(["pactl", "info"])
        if result.returncode != 0:
            return ""
        for line in result.stdout.splitlines():
            if line.startswith("Default Source:"):
                return line.split(":", 1)[1].strip()
        return ""

    def _find_mic_loop_module(self):
        result = self._run(["pactl", "list", "short", "modules"])
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            mod_id, mod_name, mod_args = parts[0], parts[1], parts[2]
            if mod_name != "module-loopback":
                continue
            if f"sink={SINK_NAME}" in mod_args and "source=soundpad_sink.monitor" not in mod_args:
                return mod_id
        return None

    def connect_input_source_to_soundpad(self, source_name):
        current = self._find_mic_loop_module()
        if current:
            unload = self._run(["pactl", "unload-module", str(current)])
            if unload.returncode != 0:
                return False, unload.stderr.strip() or unload.stdout.strip()
            self.mic_loop_module_id = None

        cmd = [
            "pactl",
            "load-module",
            "module-loopback",
            f"source={source_name}",
            f"sink={SINK_NAME}",
            "latency_msec=20",
        ]
        result = self._run(cmd)
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip()
        self.mic_loop_module_id = result.stdout.strip()
        return True, f"Mic source connected: {source_name}"

    def disconnect_input_source_from_soundpad(self):
        module_id = self._find_mic_loop_module()
        if not module_id:
            return True, "Mic source already disconnected"
        result = self._run(["pactl", "unload-module", str(module_id)])
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip()
        self.mic_loop_module_id = None
        return True, "Mic source disconnected"


class SoundpadApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("880x520")
        self.minsize(760, 420)

        self.router = AudioRouter()
        self.player_process = None

        self.profiles = {}
        self.active_profile_name = "Default"
        self.clips = []
        self.selected_index = None
        self.bound_hotkeys = []
        self.current_profile_name = tk.StringVar(value="Default")
        self.selected_input_source = tk.StringVar(value="")
        self.global_hotkeys_enabled = tk.BooleanVar(value=True)
        self.global_hotkeys_active = False
        self.global_hotkeys = GlobalHotkeyManager(self._on_global_hotkey)

        self.status_text = tk.StringVar(value="Initializing...")
        self.volume = tk.IntVar(value=100)
        self.loop_enabled = tk.BooleanVar(value=False)
        self.mic_muted = False
        self.speakers_muted = True

        self._load_settings()
        self._load_profiles()
        self._build_ui()
        self._refresh_profile_selector()
        self._refresh_listbox()
        self._bind_hotkeys()
        self._setup_audio_router()

    def _build_ui(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(root)
        left_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        right_panel = ttk.Frame(root)
        right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))

        profile_row = ttk.Frame(left_panel)
        profile_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(profile_row, text="Profile").pack(side=tk.LEFT)
        self.profile_combo = ttk.Combobox(
            profile_row,
            textvariable=self.current_profile_name,
            state="readonly",
            width=24,
        )
        self.profile_combo.pack(side=tk.LEFT, padx=(8, 0))
        self.profile_combo.bind("<<ComboboxSelected>>", self.on_profile_changed)
        ttk.Button(profile_row, text="New", command=self.create_profile).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(profile_row, text="Rename", command=self.rename_profile).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(profile_row, text="Delete", command=self.delete_profile).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(profile_row, text="Export", command=self.export_profiles).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(profile_row, text="Import", command=self.import_profiles).pack(side=tk.LEFT, padx=(6, 0))

        title = ttk.Label(left_panel, text="Sound Clips", font=("DejaVu Sans", 14, "bold"))
        title.pack(anchor="w", pady=(0, 6))

        list_frame = ttk.Frame(left_panel)
        list_frame.pack(fill=tk.BOTH, expand=True)

        self.listbox = tk.Listbox(
            list_frame,
            activestyle="none",
            font=("DejaVu Sans Mono", 11),
            selectmode=tk.SINGLE,
        )
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        self.listbox.bind("<Double-1>", lambda _e: self.play_selected())

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.configure(yscrollcommand=scrollbar.set)

        btn_frame = ttk.Frame(left_panel)
        btn_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(btn_frame, text="Add Clips", command=self.add_clips).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="Remove", command=self.remove_selected).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btn_frame, text="Play", command=self.play_selected).pack(side=tk.LEFT, padx=(20, 0))
        ttk.Button(btn_frame, text="Stop", command=self.stop_playback).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btn_frame, text="Set Hotkey", command=self.set_selected_hotkey).pack(side=tk.LEFT, padx=(20, 0))
        ttk.Button(btn_frame, text="Clear Hotkey", command=self.clear_selected_hotkey).pack(side=tk.LEFT, padx=(8, 0))

        right_title = ttk.Label(right_panel, text="Controls", font=("DejaVu Sans", 13, "bold"))
        right_title.pack(anchor="w")

        ttk.Label(right_panel, text="Volume", font=("DejaVu Sans", 11)).pack(anchor="w", pady=(14, 4))
        ttk.Scale(right_panel, from_=0, to=200, variable=self.volume, orient=tk.HORIZONTAL).pack(fill=tk.X)
        ttk.Label(right_panel, text="0% to 200%", foreground="#666").pack(anchor="w")

        ttk.Checkbutton(right_panel, text="Loop selected clip", variable=self.loop_enabled).pack(anchor="w", pady=(14, 2))
        ttk.Checkbutton(right_panel, text="Global Hotkeys (X11)", variable=self.global_hotkeys_enabled, command=self.toggle_global_hotkeys).pack(anchor="w", pady=(8, 0))
        self.mic_mute_button = ttk.Button(right_panel, text="Mute Mic", command=self.toggle_mic_mute)
        self.mic_mute_button.pack(anchor="w", fill=tk.X, pady=(12, 4))
        self.speakers_mute_button = ttk.Button(right_panel, text="Unmute Speakers", command=self.toggle_speakers_mute)
        self.speakers_mute_button.pack(anchor="w", fill=tk.X)

        ttk.Label(right_panel, text="Mic Input Source", font=("DejaVu Sans", 11)).pack(anchor="w", pady=(16, 4))
        self.mic_source_combo = ttk.Combobox(
            right_panel,
            textvariable=self.selected_input_source,
            state="readonly",
        )
        self.mic_source_combo.pack(anchor="w", fill=tk.X)
        ttk.Button(right_panel, text="Refresh Sources", command=self.refresh_input_sources).pack(anchor="w", fill=tk.X, pady=(6, 0))
        ttk.Button(right_panel, text="Connect Mic To Soundpad", command=self.connect_selected_input_source).pack(anchor="w", fill=tk.X, pady=(6, 0))
        ttk.Button(right_panel, text="Disconnect Mic From Soundpad", command=self.disconnect_input_source).pack(anchor="w", fill=tk.X, pady=(6, 0))

        info = (
            "Shortcuts:\n"
            "  Enter: Play\n"
            "  Delete: Remove\n"
            "  Alt+\\: Stop\n"
            "  Set per-clip key: Set Hotkey\n"
            "  If Alt+1 fails in dwm, use Ctrl+Alt+1"
        )
        ttk.Label(right_panel, text=info, justify=tk.LEFT, foreground="#444").pack(anchor="w", pady=(16, 0))

        source_hint = (
            f"Set input device in apps to:\n"
            f"{SOURCE_NAME}"
        )
        ttk.Label(right_panel, text=source_hint, justify=tk.LEFT, foreground="#125").pack(anchor="w", pady=(16, 0))

        status_bar = ttk.Label(self, textvariable=self.status_text, anchor="w", relief=tk.SUNKEN, padding=(8, 4))
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        self.bind("<Return>", lambda _e: self.play_selected())
        self.bind("<Delete>", lambda _e: self.remove_selected())
        self.bind("<Alt-backslash>", lambda _e: self.stop_playback())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._init_global_hotkeys)

    def _bind_hotkeys(self):
        for seq in self.bound_hotkeys:
            self.unbind_all(seq)
        self.bound_hotkeys = []

        for idx, clip in enumerate(self.clips):
            hotkey_label = clip.get("hotkey", "").strip()
            if not hotkey_label:
                continue
            sequence = self._hotkey_to_tk_sequence(hotkey_label)
            if not sequence:
                continue
            self.bind_all(sequence, lambda _e, clip_idx=idx: self.play_index(clip_idx))
            self.bound_hotkeys.append(sequence)
        self._register_global_hotkeys()

    def _init_global_hotkeys(self):
        if not self.global_hotkeys_enabled.get():
            return
        self.global_hotkeys_active = self.global_hotkeys.start()
        if not self.global_hotkeys_active:
            if not XLIB_AVAILABLE:
                self.status_text.set("Global hotkeys unavailable: install python-xlib")
            else:
                self.status_text.set("Global hotkeys unavailable on this session")
            return
        self._register_global_hotkeys()

    def _register_global_hotkeys(self):
        if not self.global_hotkeys_active:
            return
        entries = []
        for idx, clip in enumerate(self.clips):
            hotkey = clip.get("hotkey", "").strip()
            if hotkey:
                entries.append((idx, hotkey))
        entries.append(("__stop__", "Alt+\\"))
        conflicts = self.global_hotkeys.register(entries)
        if conflicts:
            self.status_text.set(f"Global hotkey conflicts: {', '.join(conflicts)}")

    def toggle_global_hotkeys(self):
        enabled = self.global_hotkeys_enabled.get()
        if enabled:
            if not self.global_hotkeys_active:
                self.global_hotkeys_active = self.global_hotkeys.start()
            if not self.global_hotkeys_active:
                self.global_hotkeys_enabled.set(False)
                messagebox.showerror("Global hotkeys unavailable", "Install python-xlib and run under X11.")
                return
            self._register_global_hotkeys()
            self.status_text.set("Global hotkeys enabled")
        else:
            if self.global_hotkeys_active:
                self.global_hotkeys.stop()
                self.global_hotkeys_active = False
            self.status_text.set("Global hotkeys disabled")

    def _on_global_hotkey(self, action):
        if action == "__stop__":
            self.after(0, self.stop_playback)
            return
        self.after(0, lambda: self.play_index(action))

    def _setup_audio_router(self):
        if not shutil.which("pactl"):
            self.status_text.set("Error: pactl not found. Install pipewire-pulse or pulseaudio.")
            return

        ok, msg = self.router.ensure_virtual_mic()
        if ok:
            ok_mute, mute_msg = self.router.unload_monitor()
            if not ok_mute:
                self.status_text.set(msg)
                messagebox.showwarning("Speaker mute default failed", mute_msg)
            else:
                self.status_text.set(f"{msg} | Speakers muted by default")
        else:
            self.status_text.set("Audio setup failed")
            messagebox.showerror("Audio setup failed", msg)
            return

        ok_mic, mic_msg = self.router.set_mic_mute(False)
        if not ok_mic:
            messagebox.showwarning("Mic control unavailable", mic_msg)
        self.refresh_input_sources()
        self.auto_connect_default_input_source()

    def _load_settings(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not SETTINGS_FILE.exists():
            return
        try:
            data = json.loads(SETTINGS_FILE.read_text())
        except Exception:
            return
        if isinstance(data, dict):
            source_name = str(data.get("input_source", "")).strip()
            if source_name:
                self.selected_input_source.set(source_name)

    def _save_settings(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {"input_source": self.selected_input_source.get().strip()}
        SETTINGS_FILE.write_text(json.dumps(data, indent=2))

    def _normalize_clip(self, clip):
        if not isinstance(clip, dict) or "path" not in clip:
            return None
        path = str(clip.get("path", "")).strip()
        if not path:
            return None
        label = str(clip.get("label", "")).strip() or Path(path).stem
        hotkey = str(clip.get("hotkey", "")).strip()
        return {"label": label, "path": path, "hotkey": hotkey}

    def _parse_profiles_payload(self, data):
        profiles = {}
        current_name = "Default"

        if isinstance(data, list):
            clips = []
            for clip in data:
                normalized = self._normalize_clip(clip)
                if normalized:
                    clips.append(normalized)
            profiles["Default"] = clips
        elif isinstance(data, dict) and isinstance(data.get("profiles"), dict):
            for name, clip_list in data["profiles"].items():
                if not isinstance(name, str) or not isinstance(clip_list, list):
                    continue
                clean_name = name.strip()
                if not clean_name:
                    continue
                clean_clips = []
                for clip in clip_list:
                    normalized = self._normalize_clip(clip)
                    if normalized:
                        clean_clips.append(normalized)
                profiles[clean_name] = clean_clips
            requested = str(data.get("current_profile", "Default")).strip()
            if requested:
                current_name = requested

        if not profiles:
            profiles = {"Default": []}
            current_name = "Default"

        if current_name not in profiles:
            current_name = sorted(profiles.keys())[0]
        return profiles, current_name

    def _load_profiles(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_FILE.exists():
            self.profiles = {"Default": []}
            self.current_profile_name.set("Default")
            self.clips = self.profiles["Default"]
            return

        try:
            data = json.loads(CONFIG_FILE.read_text())
        except Exception:
            data = None

        profiles, current_name = self._parse_profiles_payload(data)
        self.profiles = profiles
        self.active_profile_name = current_name
        self.current_profile_name.set(current_name)
        self.clips = self.profiles[current_name]

    def _save_profiles(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "current_profile": self.active_profile_name,
            "profiles": self.profiles,
        }
        CONFIG_FILE.write_text(json.dumps(data, indent=2))

    def _save_clips(self):
        current = self.active_profile_name or "Default"
        self.profiles[current] = self.clips
        self._save_profiles()

    def _refresh_profile_selector(self):
        names = sorted(self.profiles.keys())
        self.profile_combo["values"] = names
        current = self.active_profile_name
        if current not in names and names:
            current = names[0]
            self.active_profile_name = current
            self.current_profile_name.set(current)
        else:
            self.current_profile_name.set(current)
        if current in self.profiles:
            self.clips = self.profiles[current]

    def on_profile_changed(self, _event=None):
        target = self.current_profile_name.get().strip()
        if target not in self.profiles:
            self.current_profile_name.set(self.active_profile_name)
            return
        self.switch_profile(target)

    def switch_profile(self, name):
        target = name.strip()
        if target not in self.profiles:
            return
        self.stop_playback()
        current = self.active_profile_name
        if current in self.profiles:
            self.profiles[current] = self.clips
        self.active_profile_name = target
        self.current_profile_name.set(target)
        self.clips = self.profiles[target]
        self.selected_index = None
        self._save_profiles()
        self._refresh_profile_selector()
        self._refresh_listbox()
        self._bind_hotkeys()
        self.status_text.set(f"Switched profile: {target}")

    def create_profile(self):
        name = simpledialog.askstring("New profile", "Profile name:", parent=self)
        if name is None:
            return
        profile = name.strip()
        if not profile:
            return
        if profile in self.profiles:
            messagebox.showerror("Profile exists", f"Profile '{profile}' already exists.")
            return
        self.profiles[profile] = []
        self.switch_profile(profile)

    def rename_profile(self):
        old = self.active_profile_name
        if old not in self.profiles:
            return
        new_name = simpledialog.askstring("Rename profile", "New profile name:", initialvalue=old, parent=self)
        if new_name is None:
            return
        new_name = new_name.strip()
        if not new_name or new_name == old:
            return
        if new_name in self.profiles:
            messagebox.showerror("Profile exists", f"Profile '{new_name}' already exists.")
            return
        self.profiles[new_name] = self.profiles.pop(old)
        self.active_profile_name = new_name
        self.current_profile_name.set(new_name)
        self.clips = self.profiles[new_name]
        self._save_profiles()
        self._refresh_profile_selector()
        self._refresh_listbox()
        self._bind_hotkeys()
        self.status_text.set(f"Renamed profile to: {new_name}")

    def delete_profile(self):
        name = self.active_profile_name
        if name not in self.profiles:
            return
        if len(self.profiles) <= 1:
            messagebox.showerror("Cannot delete", "At least one profile is required.")
            return
        if not messagebox.askyesno("Delete profile", f"Delete profile '{name}'?"):
            return
        del self.profiles[name]
        next_name = sorted(self.profiles.keys())[0]
        self.active_profile_name = next_name
        self.current_profile_name.set(next_name)
        self.clips = self.profiles[next_name]
        self.selected_index = None
        self._save_profiles()
        self._refresh_profile_selector()
        self._refresh_listbox()
        self._bind_hotkeys()
        self.status_text.set(f"Deleted profile: {name}")

    def export_profiles(self):
        default_name = f"arch-soundpad-profiles-{int(time.time())}.json"
        out_path = filedialog.asksaveasfilename(
            title="Export profiles",
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not out_path:
            return
        payload = {
            "current_profile": self.active_profile_name,
            "profiles": self.profiles,
        }
        try:
            Path(out_path).write_text(json.dumps(payload, indent=2))
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        self.status_text.set(f"Exported profiles: {Path(out_path).name}")

    def import_profiles(self):
        in_path = filedialog.askopenfilename(
            title="Import profiles",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not in_path:
            return
        try:
            data = json.loads(Path(in_path).read_text())
            imported_profiles, imported_current = self._parse_profiles_payload(data)
        except Exception as exc:
            messagebox.showerror("Import failed", f"Invalid file: {exc}")
            return

        replace = messagebox.askyesno(
            "Import profiles",
            "Replace existing profiles?\nYes = replace all\nNo = merge",
        )
        if replace:
            self.profiles = imported_profiles
            self.active_profile_name = imported_current
        else:
            for name, clips in imported_profiles.items():
                target = name
                suffix = 2
                while target in self.profiles:
                    target = f"{name}-{suffix}"
                    suffix += 1
                self.profiles[target] = clips
            if self.active_profile_name not in self.profiles:
                self.active_profile_name = sorted(self.profiles.keys())[0]

        self.current_profile_name.set(self.active_profile_name)
        self.clips = self.profiles[self.active_profile_name]
        self.selected_index = None
        self._save_profiles()
        self._refresh_profile_selector()
        self._refresh_listbox()
        self._bind_hotkeys()
        self.status_text.set(f"Imported profiles from: {Path(in_path).name}")

    def _refresh_listbox(self):
        self.listbox.delete(0, tk.END)
        for idx, clip in enumerate(self.clips, start=1):
            label = clip.get("label") or Path(clip["path"]).stem
            hotkey = clip.get("hotkey", "").strip()
            hotkey_txt = f" [{hotkey}]" if hotkey else ""
            self.listbox.insert(tk.END, f"{idx:02d}. {label}{hotkey_txt}")

    def _on_select(self, _event):
        sel = self.listbox.curselection()
        self.selected_index = sel[0] if sel else None

    def add_clips(self):
        file_paths = filedialog.askopenfilenames(
            title="Select audio files",
            filetypes=[
                ("Audio files", "*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.opus"),
                ("All files", "*.*"),
            ],
        )
        if not file_paths:
            return

        for path in file_paths:
            p = str(Path(path).expanduser())
            label = Path(p).stem
            self.clips.append({"label": label, "path": p, "hotkey": ""})

        self._save_clips()
        self._refresh_listbox()
        self._bind_hotkeys()
        self.status_text.set(f"Added {len(file_paths)} clip(s)")

    def remove_selected(self):
        if self.selected_index is None:
            return

        del self.clips[self.selected_index]
        self.selected_index = None
        self._save_clips()
        self._refresh_listbox()
        self._bind_hotkeys()
        self.status_text.set("Removed clip")

    def play_index(self, index):
        if index < 0 or index >= len(self.clips):
            return
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(index)
        self.listbox.activate(index)
        self.selected_index = index
        self.play_selected()

    def play_selected(self):
        if self.selected_index is None:
            sel = self.listbox.curselection()
            if not sel:
                return
            self.selected_index = sel[0]

        clip = self.clips[self.selected_index]
        clip_path = clip["path"]

        if not Path(clip_path).exists():
            self.status_text.set("Clip file missing")
            messagebox.showerror("File not found", clip_path)
            return

        if not shutil.which("ffmpeg"):
            self.status_text.set("Error: ffmpeg not found")
            messagebox.showerror("Missing dependency", "Install ffmpeg")
            return

        self.stop_playback()

        volume = max(0, min(200, int(self.volume.get())))
        vol_filter = f"volume={volume / 100:.2f}"

        cmd = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
        ]
        if self.loop_enabled.get():
            cmd.extend(["-stream_loop", "-1"])
        cmd.extend(
            [
                "-i",
                clip_path,
                "-filter:a",
                vol_filter,
                "-f",
                "pulse",
                "-device",
                SINK_NAME,
                "out",
            ]
        )

        try:
            self.player_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except Exception as exc:
            self.status_text.set("Failed to play clip")
            messagebox.showerror("Playback error", str(exc))
            return

        label = clip.get("label") or Path(clip_path).name
        self.status_text.set(f"Playing: {label}")
        self.after(300, self._poll_player)

    def _poll_player(self):
        if not self.player_process:
            return
        if self.player_process.poll() is None:
            self.after(300, self._poll_player)
            return
        if self.player_process.returncode not in (0, None):
            err = ""
            if self.player_process.stderr:
                try:
                    err = self.player_process.stderr.read().strip()
                except Exception:
                    err = ""
            self.status_text.set("Playback failed")
            if err:
                messagebox.showerror("Playback failed", err)
            else:
                messagebox.showerror("Playback failed", "ffmpeg exited with an error.")
            self.player_process = None
            return
        self.player_process = None
        self.status_text.set("Idle")

    def stop_playback(self):
        if self.player_process and self.player_process.poll() is None:
            self.player_process.terminate()
            try:
                self.player_process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.player_process.kill()
        self.player_process = None
        self.status_text.set("Stopped")

    def toggle_mic_mute(self):
        target = not self.mic_muted
        ok, msg = self.router.set_mic_mute(target)
        if not ok:
            messagebox.showerror("Mic mute failed", msg)
            return
        self.mic_muted = target
        self.mic_mute_button.configure(text="Unmute Mic" if self.mic_muted else "Mute Mic")
        self.status_text.set(msg)

    def toggle_speakers_mute(self):
        target = not self.speakers_muted
        if target:
            ok, msg = self.router.unload_monitor()
        else:
            ok, msg = self.router.ensure_local_monitor()
        if not ok:
            messagebox.showerror("Speaker mute failed", msg)
            return
        self.speakers_muted = target
        self.speakers_mute_button.configure(text="Unmute Speakers" if self.speakers_muted else "Mute Speakers")
        self.status_text.set(msg)

    def _suggest_default_hotkey(self):
        used = {clip.get("hotkey", "").strip().lower() for clip in self.clips if clip.get("hotkey")}
        for i in range(1, 10):
            candidate = f"Alt+{i}"
            if candidate.lower() not in used:
                return candidate
        return ""

    def _hotkey_to_tk_sequence(self, hotkey_text):
        raw = hotkey_text.strip()
        if not raw:
            return None
        parts = [p.strip() for p in raw.split("+") if p.strip()]
        if not parts:
            return None
        key_part = parts[-1]
        mods = []
        for mod in parts[:-1]:
            lower = mod.lower()
            if lower in ("ctrl", "control"):
                mods.append("Control")
            elif lower == "alt":
                mods.append("Alt")
            elif lower == "shift":
                mods.append("Shift")
            elif lower in ("super", "win", "mod4"):
                mods.append("Mod4")
            else:
                return None
        if len(key_part) == 1:
            key_name = key_part.lower()
        else:
            key_name = key_part
        chunks = mods + [f"Key-{key_name}"]
        return "<" + "-".join(chunks) + ">"

    def set_selected_hotkey(self):
        if self.selected_index is None:
            sel = self.listbox.curselection()
            if not sel:
                messagebox.showinfo("No clip selected", "Select a clip first.")
                return
            self.selected_index = sel[0]

        clip = self.clips[self.selected_index]
        current = clip.get("hotkey", "")
        typed = simpledialog.askstring(
            "Set clip hotkey",
            "Enter key bind (examples: Alt+1, Ctrl+F, Shift+Alt+3)\nLeave empty to clear.",
            initialvalue=current,
            parent=self,
        )
        if typed is None:
            return
        typed = typed.strip()
        if typed:
            sequence = self._hotkey_to_tk_sequence(typed)
            if not sequence:
                messagebox.showerror("Invalid hotkey", "Use formats like Alt+1, Ctrl+F, Super+1, Ctrl+Alt+1.")
                return
            for idx, other in enumerate(self.clips):
                if idx == self.selected_index:
                    continue
                if other.get("hotkey", "").strip().lower() == typed.lower():
                    other["hotkey"] = ""
                    break
        clip["hotkey"] = typed
        self._save_clips()
        self._refresh_listbox()
        self._bind_hotkeys()
        self.status_text.set("Hotkey updated")

    def clear_selected_hotkey(self):
        if self.selected_index is None:
            sel = self.listbox.curselection()
            if not sel:
                return
            self.selected_index = sel[0]
        self.clips[self.selected_index]["hotkey"] = ""
        self._save_clips()
        self._refresh_listbox()
        self._bind_hotkeys()
        self.status_text.set("Hotkey cleared")

    def refresh_input_sources(self):
        sources = self.router.list_input_sources()
        self.mic_source_combo["values"] = sources
        if self.selected_input_source.get() in sources:
            return
        if sources:
            self.selected_input_source.set(sources[0])
        else:
            self.selected_input_source.set("")

    def connect_selected_input_source(self):
        source_name = self.selected_input_source.get().strip()
        if not source_name:
            messagebox.showerror("No source selected", "Select a mic source first.")
            return
        ok, msg = self.router.connect_input_source_to_soundpad(source_name)
        if not ok:
            messagebox.showerror("Mic route failed", msg)
            return
        self._save_settings()
        self.status_text.set(msg)

    def disconnect_input_source(self):
        ok, msg = self.router.disconnect_input_source_from_soundpad()
        if not ok:
            messagebox.showerror("Mic route failed", msg)
            return
        self.status_text.set(msg)

    def auto_connect_default_input_source(self):
        chosen = self.selected_input_source.get().strip()
        if not chosen:
            chosen = self.router.get_default_source().strip()
        if not chosen:
            return
        available = set(self.router.list_input_sources())
        if chosen not in available:
            return
        self.selected_input_source.set(chosen)
        ok, msg = self.router.connect_input_source_to_soundpad(chosen)
        if ok:
            self._save_settings()
            self.status_text.set(msg)
        else:
            messagebox.showwarning("Mic route warning", msg)

    def _on_close(self):
        self._save_clips()
        self.stop_playback()
        self.router.disconnect_input_source_from_soundpad()
        if self.global_hotkeys_active:
            self.global_hotkeys.stop()
            self.global_hotkeys_active = False
        self.destroy()

def main():
    app = SoundpadApp()
    app.mainloop()


if __name__ == "__main__":
    main()
