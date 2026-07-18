#!/usr/bin/env bash
# The true "start from nothing" installer: unlike bootstrap.sh (which
# already assumes git is installed), this one installs git itself,
# clones the repo, runs the normal setup, AND configures this machine
# to stay reachable headless after a reboot -- no monitor, keyboard, or
# physical login required ever again:
#   - BTRFS snapshots (snapper + grub-btrfs) for remote rollback
#     (gremlin list-snapshots / rollback-to -- see README)
#   - KDE Plasma: no sleep/suspend/hibernate, no lock screen, no login
#     screen (SDDM auto-login straight to a desktop session)
#   - `gremlin serve` installed as a systemd user service that starts
#     on login and restarts on crash
#
# Usage:
#   bash install-all.sh <your-github-repo-url>
# or, run from inside an already-cloned checkout:
#   bash install-all.sh
#
# Every section below is independently skippable if it doesn't apply
# (not Arch/Manjaro, not BTRFS, not KDE) -- this is meant to be safe to
# run on a fresh install and just as safe to re-run later.

set -e

REPO_URL="${1:-}"
TARGET_DIR="gremlin"

echo "=== Gremlin all-in-one install ==="
echo

# --- 1. Install git (and the couple of tools setup.sh needs) if missing ---
if command -v git &> /dev/null; then
    echo "[*] git already installed, skipping"
else
    echo "[*] Installing git..."
    if command -v pacman &> /dev/null; then
        sudo pacman -Sy --needed --noconfirm git
    elif command -v apt &> /dev/null; then
        sudo apt update && sudo apt install -y git
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y git
    else
        echo "[!] No supported package manager found (pacman/apt/dnf) -- install git manually and re-run."
        exit 1
    fi
fi

# --- 2. Clone the repo (or use the checkout we're already inside) ---
if [ -f "setup.sh" ] && [ -f "main.py" ]; then
    TARGET_DIR="."
    echo "[*] Already inside a Gremlin checkout, using it directly"
elif [ -d "$TARGET_DIR" ] && [ -f "$TARGET_DIR/setup.sh" ]; then
    echo "[*] $TARGET_DIR/ already exists locally, using it directly"
elif [ -n "$REPO_URL" ]; then
    echo "[*] Cloning $REPO_URL..."
    git clone "$REPO_URL" "$TARGET_DIR"
else
    read -rp "Repo URL to clone (e.g. https://github.com/you/gremlin.git): " REPO_URL
    if [ -z "$REPO_URL" ]; then
        echo "No URL given -- nothing to clone. Re-run with a URL, or from inside an existing checkout."
        exit 1
    fi
    git clone "$REPO_URL" "$TARGET_DIR"
fi

cd "$TARGET_DIR"
GREMLIN_DIR="$(pwd)"
chmod +x setup.sh
[ -f gremlin ] && chmod +x gremlin

# --- 3. Base setup: venv, dependencies, API keys (asks only for what .env doesn't already have) ---
echo
echo "[*] Running base setup (venv, dependencies, GPU detection, API keys)..."
echo
./setup.sh

# --- 4. BTRFS snapshots + grub-btrfs, for remote rollback without touching GRUB ---
# (see gremlin_core/snapshots.py -- `snapper rollback` swaps the boot
# subvolume for next boot, which is the closest real equivalent to
# picking an older entry from the GRUB menu that's actually possible
# to trigger remotely, since GRUB itself runs before any of this
# software starts.)
echo
ROOT_FSTYPE="$(findmnt -no FSTYPE / 2>/dev/null || true)"
if [ "$ROOT_FSTYPE" != "btrfs" ]; then
    echo "[*] Root filesystem isn't BTRFS ($ROOT_FSTYPE) -- skipping snapshot setup."
    echo "    (gremlin list-snapshots / rollback-to won't work on this machine.)"
elif ! command -v pacman &> /dev/null; then
    echo "[!] Snapshot setup currently only automated for pacman (Arch/Manjaro) --"
    echo "    skipping. Install snapper + grub-btrfs + snap-pac manually if you want"
    echo "    remote rollback on this distro."
else
    echo "[*] BTRFS root detected -- setting up snapper + grub-btrfs..."
    sudo pacman -S --needed --noconfirm snapper grub-btrfs snap-pac

    if ! sudo snapper list-configs 2>/dev/null | grep -q "^root "; then
        echo "[*] Creating snapper config for /..."
        sudo umount /.snapshots 2>/dev/null || true
        sudo rm -rf /.snapshots
        sudo snapper -c root create-config /
    else
        echo "[*] snapper config for / already exists, skipping"
    fi

    sudo systemctl enable --now grub-btrfs.path snapper-timeline.timer snapper-cleanup.timer
    sudo grub-mkconfig -o /boot/grub/grub.cfg

    echo "[+] Snapshot rollback ready. Once a sudo password is cached (step 6"
    echo "    below), 'gremlin list-snapshots' / 'gremlin rollback-to <N>' will work."
fi

# --- 5. KDE Plasma: never sleep, never lock, never wait at a login screen ---
# A headless box you never touch again needs a session that comes back
# up on its own after every reboot -- any one of sleep, a lock screen,
# or a login prompt waiting for a password would silently strand it
# until someone physically walks over.
echo
if [ -z "${XDG_CURRENT_DESKTOP:-}" ] && ! command -v plasmashell &> /dev/null; then
    echo "[*] KDE Plasma not detected -- skipping power/lock/login-screen setup."
else
    echo "[*] KDE Plasma detected -- disabling sleep, lock screen, and login screen..."

    # systemd-level suspend block -- belt-and-suspenders alongside the
    # KDE-specific settings below: this one holds even if a KDE power
    # setting gets reset by an update, since it's enforced by the OS,
    # not the desktop session.
    sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

    KWRITE=""
    if command -v kwriteconfig6 &> /dev/null; then
        KWRITE=kwriteconfig6
    elif command -v kwriteconfig5 &> /dev/null; then
        KWRITE=kwriteconfig5
    fi

    if [ -n "$KWRITE" ]; then
        # Screen locker off entirely.
        "$KWRITE" --file kscreenlockerrc --group Daemon --key Autolock false
        "$KWRITE" --file kscreenlockerrc --group Daemon --key LockOnResume false

        # No suspend / screen-off on idle, on AC or battery.
        for PROFILE in AC Battery; do
            "$KWRITE" --file powermanagementprofilesrc --group "$PROFILE" --group SuspendSession --key idleTime 0
            "$KWRITE" --file powermanagementprofilesrc --group "$PROFILE" --group SuspendSession --key suspendThenHibernate false
            "$KWRITE" --file powermanagementprofilesrc --group "$PROFILE" --group DimDisplay --key idleTime 0
            "$KWRITE" --file powermanagementprofilesrc --group "$PROFILE" --group DPMSControl --key idleTime 0
        done
        echo "[+] Lock screen and idle suspend/dim disabled"
    else
        echo "[!] kwriteconfig5/6 not found -- skipped the KDE-specific settings"
        echo "    (systemd sleep/suspend/hibernate are still masked above)"
    fi

    # SDDM auto-login -- no login screen to wait at after a reboot.
    if command -v sddm &> /dev/null || [ -d /etc/sddm.conf.d ]; then
        SESSION_NAME="plasma"
        for CANDIDATE in /usr/share/wayland-sessions/plasma.desktop /usr/share/xsessions/plasma.desktop \
                         /usr/share/wayland-sessions/plasmax11.desktop; do
            if [ -f "$CANDIDATE" ]; then
                SESSION_NAME="$(basename "$CANDIDATE" .desktop)"
                break
            fi
        done
        sudo mkdir -p /etc/sddm.conf.d
        sudo tee /etc/sddm.conf.d/gremlin-autologin.conf > /dev/null <<EOF
[Autologin]
User=$USER
Session=$SESSION_NAME
EOF
        echo "[+] SDDM auto-login configured for $USER ($SESSION_NAME)"
    else
        echo "[*] SDDM not found -- skipped auto-login (set it up manually if you use a different login manager)"
    fi
fi

# --- 6. Cache a sudo password so root commands can run remotely later ---
echo
echo "[*] To run root-requiring commands remotely (no monitor needed), Gremlin"
echo "    caches a verified sudo password locally (never sent over the network)."
read -rp "Set that up now? (y/N): " SETUP_SUDO
if [[ "$SETUP_SUDO" =~ ^[Yy]$ ]]; then
    source venv/bin/activate
    python main.py set-sudo-password
fi

# --- 7. gremlin serve as a systemd --user service: starts on login, restarts on crash ---
echo
read -rp "Install 'gremlin serve' as an auto-starting systemd --user service? (Y/n): " SETUP_SERVICE
if [[ ! "$SETUP_SERVICE" =~ ^[Nn]$ ]]; then
    mkdir -p ~/.config/systemd/user
    # WantedBy=multi-user.target is for the SYSTEM manager -- a --user
    # unit needs default.target instead, or `enable` silently creates a
    # symlink nothing ever activates and it never actually autostarts.
    sed -e "s|^User=.*|User=$USER|" \
        -e "s|^WorkingDirectory=.*|WorkingDirectory=$GREMLIN_DIR|" \
        -e "s|^ExecStart=.*|ExecStart=$GREMLIN_DIR/venv/bin/python main.py serve|" \
        -e "s|^WantedBy=multi-user.target|WantedBy=default.target|" \
        deploy/gremlin.service > ~/.config/systemd/user/gremlin.service
    systemctl --user daemon-reload
    systemctl --user enable --now gremlin.service
    # Lingering keeps the --user service (and its timers) running even
    # if this user session ends without SDDM auto-login kicking in --
    # a second line of defense for "never needs a monitor again."
    sudo loginctl enable-linger "$USER" 2>/dev/null || true
    echo "[+] gremlin.service enabled -- check status with: systemctl --user status gremlin"
fi

echo
echo "=== All-in-one install complete ==="
echo
echo "config/models.yaml currently expects 5 local models -- see README or"
echo "bootstrap.sh's output for the exact 'python main.py models --hf ...' searches."
echo
echo "Remote admin/reboot token: gremlin admin-token"
echo "Rollback if something breaks: gremlin list-snapshots / gremlin rollback-to <N>"
