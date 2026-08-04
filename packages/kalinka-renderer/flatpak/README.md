# Kalinka Renderer as a flatpak

Build (needs `flatpak-builder` and the flathub remote):

```sh
flatpak-builder --force-clean --user --install --install-deps-from=flathub \
  build-flatpak io.github.madenvel.KalinkaRenderer.yml
flatpak run io.github.madenvel.KalinkaRenderer
```

Or produce a distributable single-file bundle:

```sh
flatpak-builder --force-clean --repo=repo build-flatpak io.github.madenvel.KalinkaRenderer.yml
flatpak build-bundle repo kalinka-renderer.flatpak io.github.madenvel.KalinkaRenderer
```

The sandbox gets `--device=all` (raw ALSA access to `hw:` devices) and
network (mDNS discovery + audio streaming). The renderer id persists in
`~/.var/app/io.github.madenvel.KalinkaRenderer/data/`.

## Autostart / service lifecycle

Flatpak apps cannot register systemd *system* services — that is a hard
platform limitation, so the deb/rpm packages remain the right choice for
headless appliances. What flatpak supports is a systemd **user** service:
`kalinka-renderer.service` in this directory wraps `flatpak run`/`flatpak
kill`; install it per the comments inside, and `loginctl enable-linger`
makes it start at boot without anyone logging in.
