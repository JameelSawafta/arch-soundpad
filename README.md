# Arch Soundpad (dwm-friendly)

Simple **Soundpad-like application for Arch Linux** with a GUI.

It allows you to trigger sound effects and route them through a **virtual microphone** so they can be used in **Discord, OBS, games, or voice chat applications**.

---

## Features

- GUI soundpad for Linux
- Add, play, and stop sound clips
- Virtual microphone output (`soundpad_mic`)
- Works with Discord, OBS, games, etc.
- Global hotkeys (X11 compatible, works with dwm)
- Custom hotkeys per sound clip
- Multiple profiles
- Profile export/import via JSON
- Automatic profile backups
- Push-to-talk microphone routing
- Diagnostics tool for troubleshooting

---

## How It Works

The application:

1. Creates a **virtual sink** called `soundpad_sink`.
2. Creates a **virtual microphone** called `soundpad_mic`.
3. Plays audio clips into the virtual sink.
4. Routes the sink to the virtual microphone so other apps can use it.

You simply select **`soundpad_mic`** as your microphone in Discord or other apps.

---

# Dependencies (Arch Linux)

Install dependencies:

```bash
sudo pacman -Syu python tk ffmpeg pipewire pipewire-pulse python-xlib
```

If you see:

```
ImportError: libtk8.6.so
```

Your system likely has a **partial upgrade**.

Fix it with:

```bash
sudo pacman -Syu
```

---

# Installation

Clone the repository:

```bash
git clone https://github.com/YOUR_USERNAME/arch-soundpad.git
cd arch-soundpad
```

Run the application:

```bash
./run.sh
```

---

# Install as Desktop Application

```bash
./install.sh
```

This creates:

```
~/.local/bin/arch-soundpad
~/.local/share/applications/arch-soundpad.desktop
```

You can then launch it from your **desktop environment launcher**.

---

# Usage

1. Launch the app.
2. Click **Add Clips** and choose audio files.
3. In Discord/OBS, select input device:

```
soundpad_mic
```

4. Select your real microphone from **Mic Input Source**.
5. Click **Connect Mic To Soundpad**.
6. Press **Play** on a clip.

Optional features:

- Select a clip and click **Set Hotkey**
- Enable **Global Hotkeys (X11)** to trigger clips while unfocused
- Use **Profiles** to organize sound sets
- Export or import profiles as JSON

---

# Hotkeys

Default controls:

| Key | Action |
|----|----|
| Enter | Play selected clip |
| Delete | Remove selected clip |
| Alt + \\ | Stop playback |

Clip hotkeys are **user configurable**.

Global hotkeys work on **X11 window managers** like **dwm** when `python-xlib` is installed.

---

# Profiles

Profiles allow different soundboards.

Profile actions:

- Create new profile
- Rename profile
- Delete profile
- Switch profiles

---

# Profile Export / Import

Profiles can be saved and shared as JSON.

Export contains:

```
current_profile
profiles
clips
hotkeys
```

Import modes:

**Replace mode**

- Replaces all existing profiles.

**Merge mode**

- Keeps existing profiles.
- Adds imported ones.
- Name conflicts automatically become:

```
profile
profile-2
profile-3
```

---

# Automatic Backups

Each profile save automatically creates a backup.

Backup location:

```
~/.config/arch-soundpad/backups/
```

Backups are timestamped.

You can restore them using the **Restore Backup** button.

---

# Configuration Files

Clips and settings are stored in:

```
~/.config/arch-soundpad/clips.json
```

---

# Diagnostics Tool

The **Diagnostics** button checks:

- PipeWire / PulseAudio status
- Virtual device routing
- Hotkey system
- Profile configuration

Useful for troubleshooting.

---

# Development

Clone the repository:

```bash
git clone https://github.com/jameelsawafta/arch-soundpad.git
cd arch-soundpad
```

Run quick test:

```bash
./smoke_test.sh
```

Headless check:

```bash
python3 soundpad_app.py --headless-check
```

---

# AUR Package

Install from AUR:

```bash
yay -S arch-soundpad
```

Manual build:

```bash
git clone https://aur.archlinux.org/arch-soundpad.git
cd arch-soundpad
makepkg -si
```

The AUR package downloads source from **GitHub release tarballs**.

---

# Notes

- Playback uses **ffmpeg**
- Audio routing uses **PipeWire / PulseAudio**
- Designed for **Arch Linux**
- Global hotkeys currently support **X11 environments**