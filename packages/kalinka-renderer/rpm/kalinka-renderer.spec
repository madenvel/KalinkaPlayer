# Built in-place from the source tree (no tarball):
#   rpmbuild -bb --build-in-place --define "renderer_version X.Y.Z" \
#     rpm/kalinka-renderer.spec
# See scripts/build_rpm.sh. Symbols land in the -debuginfo subpackage, so the
# main package ships a stripped binary.

Name:           kalinka-renderer
Version:        %{renderer_version}
Release:        1%{?dist}
Summary:        Kalinka Music Player network audio renderer
License:        GPL-3.0-or-later
URL:            https://github.com/madenvel/KalinkaPlayer

BuildRequires:  cmake >= 3.16
BuildRequires:  gcc-c++
BuildRequires:  protobuf-devel
BuildRequires:  protobuf-compiler
BuildRequires:  boost-devel
BuildRequires:  spdlog-devel
BuildRequires:  alsa-lib-devel
BuildRequires:  libcurl-devel
BuildRequires:  curlpp-devel
BuildRequires:  flac-devel
BuildRequires:  pkgconf-pkg-config
BuildRequires:  systemd-rpm-macros
%{?systemd_requires}

%description
Network audio renderer for the Kalinka Music Player. Discovers a Kalinka
server over mDNS, accepts a playback session and plays audio through the
local ALSA device.

%build
%cmake -DCMAKE_BUILD_TYPE=Release
%cmake_build --target kalinka-renderer

%install
%cmake_install
install -D -m 644 scripts/kalinka-renderer.service %{buildroot}%{_unitdir}/kalinka-renderer.service
install -D -m 644 rpm/kalinka-renderer.sysusers %{buildroot}%{_sysusersdir}/kalinka-renderer.conf

%pre
%sysusers_create_compat rpm/kalinka-renderer.sysusers

%post
%systemd_post kalinka-renderer.service
# Mirror the deb: a fresh install brings the renderer up immediately.
if [ $1 -eq 1 ]; then
    systemctl enable --now kalinka-renderer.service || :
fi

%preun
%systemd_preun kalinka-renderer.service

%postun
%systemd_postun_with_restart kalinka-renderer.service

%files
%{_bindir}/kalinka-renderer
%{_unitdir}/kalinka-renderer.service
%{_sysusersdir}/kalinka-renderer.conf

%changelog
* Tue Aug 04 2026 Dmitry Savin <envelsavinds@gmail.com> - 0.1.0-1
- Initial package
