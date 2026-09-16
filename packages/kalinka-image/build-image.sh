#!/usr/bin/env bash
#
# Build a bootable Kalinka Player appliance image: a minimal Debian with the
# server, every first-party plugin, the browser player and the renderer already
# installed and enabled, ready to play as soon as it is powered on.
#
# Usage:
#   sudo ./build-image.sh <target> [kalinka-version]
#
# Targets live in targets/*.sh and are the only place that knows about
# partition tables and bootloaders. Each one sets:
#
#   TARGET_ARCH              dpkg architecture to debootstrap
#   TARGET_PARTITION_LAYOUT  an sfdisk script
#   TARGET_BOOT_PART         1-based index of the FAT partition
#   TARGET_ROOT_PART         1-based index of the root partition
#   TARGET_BOOT_MOUNT        where the FAT partition is mounted
#   TARGET_BOOT_FSTAB_OPTS   its fstab mount options
#   TARGET_PACKAGES          kernel, firmware and bootloader packages
#   target_install_bootloader()  make the image bootable, and prove it did
#
# Env:
#   SUITE, MIRROR       Debian suite and mirror (default: trixie, deb.debian.org)
#   IMAGE_SIZE          image size before first-boot growth (default: 4GiB)
#   OUT_DIR             where the .img.xz lands (default: ./out)
#   XZ_LEVEL            xz compression preset (default: -6)
#   GITHUB_TOKEN        optional, raises the GitHub API rate limit
#
# Building for another architecture needs qemu-user-static registered with
# binfmt_misc on the host; the check below says so if it is missing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

SUITE="${SUITE:-trixie}"
MIRROR="${MIRROR:-http://deb.debian.org/debian}"
IMAGE_SIZE="${IMAGE_SIZE:-4GiB}"
OUT_DIR="${OUT_DIR:-$SCRIPT_DIR/out}"
XZ_LEVEL="${XZ_LEVEL:--6}"

ROOT_LABEL=kalinka-root
BOOT_LABEL=KALINKA-BT

# Everything the appliance needs that no Kalinka package depends on: a way in,
# a way onto the network, a way to grow into its card, and the tools whose
# absence turns "no sound" into a mystery.
BASE_PACKAGES="ca-certificates curl openssl sudo openssh-server
               network-manager iw wireless-regdb
               cloud-guest-utils dosfstools e2fsprogs
               systemd-timesyncd dbus tzdata
               python3 python3-venv python3-pip
               alsa-utils"

die() { echo "build-image: $*" >&2; exit 1; }

log() { echo; echo "==> $*"; }

in_chroot() {
  chroot "$ROOTFS" env -i \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8 HOME=/root \
    ${GITHUB_TOKEN:+GITHUB_TOKEN="$GITHUB_TOKEN"} "$@"
}

apt_install() {
  # Recommends stay on: fpcalc reaches the image as a Recommends of the
  # localfiles plugin, and so do the codec and firmware odds and ends the
  # packages below only suggest they want.
  in_chroot apt-get install -y "$@"
}

TARGET="${1:-}"
[ -n "$TARGET" ] || die "usage: build-image.sh <target> [kalinka-version]"
[ -r "$SCRIPT_DIR/targets/$TARGET.sh" ] || die "unknown target '$TARGET'"
KALINKA_VERSION="${2:-}"

# shellcheck source=/dev/null
. "$SCRIPT_DIR/targets/$TARGET.sh"

[ "$(id -u)" -eq 0 ] || die "must run as root (loop devices, mounts, chroot)"
for tool in debootstrap sfdisk losetup mkfs.ext4 mkfs.vfat blkid xz; do
  command -v "$tool" >/dev/null || die "missing host tool: $tool"
done
# A debootstrap older than the suite it is asked for has no script for it,
# and every Debian suite script is the same file.
[ -e "/usr/share/debootstrap/scripts/$SUITE" ] || die \
  "this debootstrap has no script for $SUITE: ln -s sid /usr/share/debootstrap/scripts/$SUITE"

case "$(uname -m)" in
  x86_64)  HOST_ARCH=amd64 ;;
  aarch64) HOST_ARCH=arm64 ;;
  *) die "unsupported build host architecture: $(uname -m)" ;;
esac
if [ "$HOST_ARCH" != "$TARGET_ARCH" ]; then
  qemu_arch=$([ "$TARGET_ARCH" = arm64 ] && echo aarch64 || echo x86_64)
  [ -e "/proc/sys/fs/binfmt_misc/qemu-$qemu_arch" ] || die \
    "building $TARGET_ARCH on $HOST_ARCH needs qemu-user-static registered with binfmt_misc"
fi

WORK="$(mktemp -d)"
ROOTFS="$WORK/rootfs"
IMAGE="$WORK/kalinka.img"
LOOP=""
MOUNTED=()

cleanup() {
  local status=$?
  set +e
  local i
  for (( i=${#MOUNTED[@]}-1 ; i>=0 ; i-- )); do
    umount -l "${MOUNTED[i]}" 2>/dev/null
  done
  [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null
  rm -rf "$WORK"
  exit $status
}
trap cleanup EXIT

mount_at() { mount "$@" && MOUNTED+=("${*: -1}"); }

log "Creating a $IMAGE_SIZE image and its partitions"
truncate -s "$IMAGE_SIZE" "$IMAGE"
printf '%s\n' "$TARGET_PARTITION_LAYOUT" | sfdisk --quiet "$IMAGE"
LOOP="$(losetup --find --show --partscan "$IMAGE")"
BOOT_DEV="${LOOP}p${TARGET_BOOT_PART}"
ROOT_DEV="${LOOP}p${TARGET_ROOT_PART}"
for _ in $(seq 50); do [ -b "$ROOT_DEV" ] && break; sleep 0.1; done
[ -b "$ROOT_DEV" ] || die "the kernel did not expose $ROOT_DEV"

mkfs.vfat -F 32 -n "$BOOT_LABEL" "$BOOT_DEV" >/dev/null
# orphan_file and metadata_csum_seed are newer than some of the boot paths
# that have to read this filesystem, and a host with fresher e2fsprogs than
# the image would otherwise bake them in without being asked.
mkfs.ext4 -q -L "$ROOT_LABEL" -O '^orphan_file,^metadata_csum_seed' "$ROOT_DEV"

mkdir -p "$ROOTFS"
mount_at "$ROOT_DEV" "$ROOTFS"

log "Debootstrapping Debian $SUITE ($TARGET_ARCH)"
debootstrap --arch="$TARGET_ARCH" \
  --components=main,contrib,non-free-firmware \
  "$SUITE" "$ROOTFS" "$MIRROR"

mkdir -p "$ROOTFS$TARGET_BOOT_MOUNT"
mount_at "$BOOT_DEV" "$ROOTFS$TARGET_BOOT_MOUNT"
mount_at --bind /dev "$ROOTFS/dev"
mount_at --bind /dev/pts "$ROOTFS/dev/pts"
mount_at -t proc proc "$ROOTFS/proc"
mount_at -t sysfs sys "$ROOTFS/sys"
mount_at -t tmpfs tmpfs "$ROOTFS/run"

log "Configuring the base system"
cat > "$ROOTFS/etc/apt/sources.list.d/debian.sources" <<SOURCES
Types: deb
URIs: $MIRROR
Suites: $SUITE $SUITE-updates
Components: main contrib non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://security.debian.org/debian-security
Suites: $SUITE-security
Components: main contrib non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
SOURCES
: > "$ROOTFS/etc/apt/sources.list"

printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\n' > "$ROOTFS/etc/resolv.conf"
echo kalinka > "$ROOTFS/etc/hostname"
printf '127.0.0.1\tlocalhost\n127.0.1.1\tkalinka\n::1\tlocalhost ip6-localhost ip6-loopback\n' \
  > "$ROOTFS/etc/hosts"
echo 'LANG=C.UTF-8' > "$ROOTFS/etc/default/locale"

cat > "$ROOTFS/etc/fstab" <<FSTAB
LABEL=$ROOT_LABEL	/	ext4	defaults,noatime	0	1
LABEL=$BOOT_LABEL	$TARGET_BOOT_MOUNT	vfat	$TARGET_BOOT_FSTAB_OPTS	0	2
FSTAB

# A journal that grows without a ceiling is what filled the Pi this image
# replaces; the appliance keeps a week or so of logs and no more.
mkdir -p "$ROOTFS/etc/systemd/journald.conf.d"
printf '[Journal]\nSystemMaxUse=200M\n' \
  > "$ROOTFS/etc/systemd/journald.conf.d/kalinka.conf"

# Nothing behind this chroot answers to systemctl; build-aids/systemctl says
# what stands in for it and why.
printf '#!/bin/sh\nexit 101\n' > "$ROOTFS/usr/sbin/policy-rc.d"
chmod 755 "$ROOTFS/usr/sbin/policy-rc.d"
in_chroot dpkg-divert --local --rename --divert /usr/bin/systemctl.real \
  --add /usr/bin/systemctl
install -m 755 "$SCRIPT_DIR/build-aids/systemctl" "$ROOTFS/usr/bin/systemctl"

in_chroot apt-get update

log "Installing the kernel, firmware and bootloader"
apt_install $TARGET_PACKAGES

log "Installing the base system packages"
apt_install $BASE_PACKAGES

log "Installing Kalinka"
install -d "$ROOTFS/tmp/kalinka-install"
install -m 755 "$REPO_ROOT/scripts/install-release.sh" \
  "$REPO_ROOT/scripts/install-renderer.sh" "$ROOTFS/tmp/kalinka-install/"
in_chroot env NO_APT_UPDATE=1 /tmp/kalinka-install/install-release.sh $KALINKA_VERSION
rm -rf "$ROOTFS/tmp/kalinka-install"

# The server builds its venv from the shipped wheels on first start. Doing it
# here is what lets the appliance come up and play on a network that cannot
# reach PyPI — and saves the first boot several minutes on a Pi.
log "Pre-building the server venv"
in_chroot /opt/kalinka/bootstrap.sh
[ -x "$ROOTFS/opt/kalinka/venv/bin/kalinka-server" ] \
  || die "bootstrap.sh left no kalinka-server in the venv"
[ -x "$ROOTFS/usr/bin/fpcalc" ] \
  || die "fpcalc is missing — the localfiles plugin's Recommends did not install"

log "Installing the first-boot machinery"
cp -a "$SCRIPT_DIR/rootfs/." "$ROOTFS/"
echo "KALINKA_IMAGE_BOOT=$TARGET_BOOT_MOUNT" > "$ROOTFS/etc/default/kalinka-image"
install -m 644 "$SCRIPT_DIR/boot/kalinka-firstboot.conf.example" \
  "$ROOTFS$TARGET_BOOT_MOUNT/"
in_chroot systemctl enable kalinka-growroot.service kalinka-firstboot.service

target_install_bootloader

log "Stripping this build's identity out of the image"
rm -f "$ROOTFS/usr/bin/systemctl"
in_chroot dpkg-divert --local --rename --divert /usr/bin/systemctl.real \
  --remove /usr/bin/systemctl
rm -f "$ROOTFS/usr/sbin/policy-rc.d"
in_chroot apt-get clean
rm -rf "$ROOTFS"/var/lib/apt/lists/* "$ROOTFS"/tmp/*
# Emptied rather than removed: something expects most of these files to exist,
# and the one that recreates them is rarely the one that reads them.
find "$ROOTFS/var/log" -type f -delete
# bootstrap.sh pointed pip's cache here, so this is a few hundred megabytes of
# wheels already installed. systemd recreates it, owned by the service, on the
# first start.
rm -rf "$ROOTFS/var/cache/kalinka"
# Secrets and identities a published image must not hand to every machine
# that flashes it. kalinka-firstboot.service makes new ones on first boot.
rm -f "$ROOTFS"/etc/ssh/ssh_host_*
: > "$ROOTFS/etc/machine-id"
rm -f "$ROOTFS/var/lib/dbus/machine-id"
printf 'nameserver 1.1.1.1\n' > "$ROOTFS/etc/resolv.conf"

# xz stores a run of zeros in almost nothing and an unwritten ext4 block in
# whatever the host left there, so the difference is most of the download.
dd if=/dev/zero of="$ROOTFS/zero" bs=4M status=none || true
rm -f "$ROOTFS/zero"
sync

log "Compressing"
VERSION="$(in_chroot dpkg-query -W -f='${Version}' kalinka-server)"
NAME="kalinka-$VERSION-$TARGET-$TARGET_ARCH.img"
mkdir -p "$OUT_DIR"
for (( i=${#MOUNTED[@]}-1 ; i>=0 ; i-- )); do umount "${MOUNTED[i]}"; done
MOUNTED=()
losetup -d "$LOOP"
LOOP=""
xz "$XZ_LEVEL" --threads=0 --stdout "$IMAGE" > "$OUT_DIR/$NAME.xz"
( cd "$OUT_DIR" && sha256sum "$NAME.xz" > "$NAME.xz.sha256" )

log "Built $OUT_DIR/$NAME.xz"
ls -lh "$OUT_DIR/$NAME.xz"
