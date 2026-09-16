# Raspberry Pi 4 / 400 / CM4, Debian arm64.
#
# The Pi's own firmware is the bootloader: it reads config.txt from a FAT
# partition and starts the kernel directly, so there is no GRUB here. Debian's
# raspi-firmware package owns that partition — its kernel hooks copy vmlinuz
# and the initrd across and regenerate config.txt and cmdline.txt on every
# kernel upgrade, which is what keeps the machine bootable after an apt
# upgrade rather than only on the day it was imaged.

# shellcheck shell=bash disable=SC2034  # build-image.sh reads these after sourcing
TARGET_ARCH=arm64
TARGET_PARTITION_LAYOUT='label: dos
unit: sectors
start=8192, size=1048576, type=c, bootable
start=1056768, type=83'
TARGET_BOOT_PART=1
TARGET_ROOT_PART=2
TARGET_BOOT_MOUNT=/boot/firmware
TARGET_BOOT_FSTAB_OPTS="defaults,flush,umask=077"
TARGET_PACKAGES="linux-image-arm64 raspi-firmware firmware-brcm80211"

target_install_bootloader() {
  # The hook sources this file, so a block at the end is the whole override.
  # Two of these three settings are only wrong because the image is built off
  # the device: the hook decides them by looking at hardware that is not here.
  #
  #   CMA      the hook skips it on a Pi 4 and warns that setting it anyway
  #            "might render your system unbootable"; it cannot see a Pi 4
  #            from a build machine, so it would set it.
  #   CONSOLES "auto" means whichever of ttyAMA0 and ttyS1 has a device node,
  #            which here is whatever the build host happens to own. ttyS1 is
  #            the Pi 4's UART, and tty0 is the screen someone plugs in.
  cat >> "$ROOTFS/etc/default/raspi-firmware" <<DEFAULTS

# Set when this image was built.
ROOTPART=LABEL=$ROOT_LABEL
CMA=0
CONSOLES="tty0 ttyS1,115200"
DEFAULTS
  in_chroot dpkg-reconfigure -f noninteractive raspi-firmware

  local boot="$ROOTFS$TARGET_BOOT_MOUNT" f
  for f in config.txt cmdline.txt start4.elf fixup4.dat; do
    [ -s "$boot/$f" ] || die "raspi-firmware left no $f on the boot partition"
  done
  compgen -G "$boot/vmlinuz-*" >/dev/null || die "no kernel on the boot partition"
  compgen -G "$boot/initrd.img-*" >/dev/null || die "no initrd on the boot partition"
  compgen -G "$boot/bcm2711-rpi-4*.dtb" >/dev/null \
    || die "no Pi 4 device tree on the boot partition"
  grep -q "root=LABEL=$ROOT_LABEL" "$boot/cmdline.txt" \
    || die "cmdline.txt does not point at LABEL=$ROOT_LABEL"
  grep -q "cma=" "$boot/cmdline.txt" \
    && die "cmdline.txt sets cma, which a Pi 4 may refuse to boot with"
  return 0
}
