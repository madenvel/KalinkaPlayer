#!/usr/bin/env bash
#
# Apply the operator's configuration from the boot partition.
#
# Two jobs with different lifetimes, which is why the unit carries no
# condition and the decision lives here. Giving the machine an identity of its
# own — host keys, machine-id — happens once, and is what keeps a published
# image from handing every machine that flashes it the same secrets. Applying
# a configuration file happens whenever one is there, so the same file put
# back on a machine already in service is a way in when there is no shell.
set -euo pipefail

CONF_NAME=kalinka-firstboot.conf
STAMP=/var/lib/kalinka-image/firstboot-done
ISSUE=/etc/issue.d/10-kalinka.issue

# shellcheck source=/dev/null
. /etc/default/kalinka-image
CONF="$KALINKA_IMAGE_BOOT/$CONF_NAME"

log() { echo "kalinka-firstboot: $*"; }

take_own_identity() {
  rm -f /etc/ssh/ssh_host_*_key /etc/ssh/ssh_host_*_key.pub
  ssh-keygen -A
  : > /etc/machine-id
  mkdir -p "$(dirname "$STAMP")"
  : > "$STAMP"
}

set_hostname() {
  local name="$1"
  hostnamectl set-hostname "$name"
  # Rewritten in place rather than replaced: /etc/hosts is a bind mount often
  # enough that swapping the inode under it is not worth the tidier code.
  local hosts
  hosts="$(sed -E "s/^(127\.0\.1\.1[[:space:]]+).*/\1$name/" /etc/hosts)"
  printf '%s\n' "$hosts" > /etc/hosts
  grep -q '^127\.0\.1\.1' /etc/hosts || echo "127.0.1.1	$name" >> /etc/hosts
}

create_account() {
  local name="$1" hash="$2" key="$3"
  id -u "$name" >/dev/null 2>&1 || useradd -m -s /bin/bash "$name"
  usermod -aG sudo "$name"
  if [ -n "$hash" ]; then
    usermod -p "$hash" "$name"
  else
    passwd -l "$name" >/dev/null
  fi
  if [ -n "$key" ]; then
    local home
    home="$(getent passwd "$name" | cut -d: -f6)"
    install -d -m 700 -o "$name" -g "$name" "$home/.ssh"
    printf '%s\n' "$key" > "$home/.ssh/authorized_keys"
    chown "$name:$name" "$home/.ssh/authorized_keys"
    chmod 600 "$home/.ssh/authorized_keys"
  fi
}

configure_wifi() {
  local ssid="$1" psk="$2" country="$3"
  if [ -n "$country" ]; then
    mkdir -p /etc/modprobe.d
    echo "options cfg80211 ieee80211_regdom=$country" > /etc/modprobe.d/kalinka-regdom.conf
    iw reg set "$country" || log "could not apply the $country regulatory domain now; it takes effect on reboot"
  fi
  # NetworkManager ignores a profile any other user could read.
  local profile=/etc/NetworkManager/system-connections/kalinka-wifi.nmconnection
  install -D -m 600 /dev/null "$profile"
  cat > "$profile" <<PROFILE
[connection]
id=kalinka-wifi
type=wifi
autoconnect=true

[wifi]
mode=infrastructure
ssid=$ssid

[wifi-security]
key-mgmt=wpa-psk
psk=$psk

[ipv4]
method=auto

[ipv6]
method=auto
PROFILE
  nmcli connection reload
  nmcli connection up kalinka-wifi || log "Wi-Fi did not associate yet; NetworkManager will keep retrying"
}

apply_configuration() {
  HOSTNAME=kalinka
  USERNAME=
  PASSWORD=
  PASSWORD_HASH=
  SSH_AUTHORIZED_KEY=
  SSH_ENABLE=1
  WIFI_SSID=
  WIFI_PASSWORD=
  WIFI_COUNTRY=
  TIMEZONE=

  # shellcheck source=/dev/null
  . "$CONF"

  set_hostname "$HOSTNAME"
  [ -n "$TIMEZONE" ] && timedatectl set-timezone "$TIMEZONE"

  if [ -n "$USERNAME" ]; then
    if [ -z "$PASSWORD_HASH" ] && [ -n "$PASSWORD" ]; then
      PASSWORD_HASH="$(openssl passwd -6 "$PASSWORD")"
    fi
    create_account "$USERNAME" "$PASSWORD_HASH" "$SSH_AUTHORIZED_KEY"
    if [ -z "$PASSWORD_HASH" ] && [ -z "$SSH_AUTHORIZED_KEY" ]; then
      log "$USERNAME has neither a password nor a key and cannot log in"
    else
      log "created $USERNAME"
    fi
  fi

  # Nothing enables ssh: openssh-server arrives enabled and the image keeps
  # the distro's arrangement of socket versus service. Only the refusal is
  # ours.
  if [ "$SSH_ENABLE" != 1 ]; then
    systemctl disable --now ssh.socket ssh.service || true
    log "ssh disabled"
  fi

  [ -n "$WIFI_SSID" ] && configure_wifi "$WIFI_SSID" "$WIFI_PASSWORD" "$WIFI_COUNTRY"

  # The boot partition is FAT with no permissions to hide behind, and it is
  # the first thing visible when the media is plugged into another machine.
  # Whatever secrets this file carried do not stay there.
  shred -u "$CONF" 2>/dev/null || rm -f "$CONF"
  return 0
}

# \4 is agetty's escape for this machine's address, filled in per console.
announce_status() {
  mkdir -p "$(dirname "$ISSUE")"
  if getent passwd | awk -F: '$3 >= 1000 && $3 < 65534 { found = 1 } END { exit !found }'; then
    cat > "$ISSUE" <<NOTICE
Kalinka Player is running: open http://\4:8000 in a browser.

NOTICE
  else
    cat > "$ISSUE" <<NOTICE
Kalinka Player is running: open http://\4:8000 in a browser.
No login account exists on this machine. To create one, write $CONF_NAME to
the boot partition (see the example file beside it) and reboot.

NOTICE
  fi
}

[ -e "$STAMP" ] || take_own_identity
if [ -r "$CONF" ]; then
  apply_configuration
else
  log "no $CONF_NAME on the boot partition — nothing to apply"
fi
announce_status
