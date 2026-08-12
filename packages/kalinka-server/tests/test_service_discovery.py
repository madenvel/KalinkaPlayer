"""Per-interface mDNS announcements: one single-address instance each, grouped
by clients through the server_id TXT value."""

import socket

import pytest

from kalinka_server import service_discovery
from kalinka_server.config_model import KalinkaConfig
from kalinka_server.service_discovery import ServiceDiscovery, get_service_info

SERVER_ID = "9f1c9f2e-1111-2222-3333-444455556666"


@pytest.fixture(autouse=True)
def stable_server_id(monkeypatch):
    monkeypatch.setattr(service_discovery, "get_server_id", lambda: SERVER_ID)


@pytest.fixture
def two_interfaces(monkeypatch):
    monkeypatch.setattr(
        service_discovery,
        "get_interface_ip_mappings",
        lambda: {"eth0": "192.168.1.20", "wlan0": "10.20.0.15"},
    )


def addresses_of(info):
    return [socket.inet_ntoa(addr) for addr in info.addresses]


def test_one_single_address_instance_per_interface(two_interfaces):
    discovery = ServiceDiscovery(KalinkaConfig())

    assert [a.interface for a in discovery.announcements] == ["eth0", "wlan0"]
    by_interface = {a.interface: a for a in discovery.announcements}
    assert addresses_of(by_interface["eth0"].service_info) == ["192.168.1.20"]
    assert addresses_of(by_interface["wlan0"].service_info) == ["10.20.0.15"]


def test_instance_names_carry_the_interface_suffix(two_interfaces):
    discovery = ServiceDiscovery(KalinkaConfig())

    assert [a.service_info.name for a in discovery.announcements] == [
        "My Kalinka Service (eth0)._kalinkaplayer._tcp.local.",
        "My Kalinka Service (wlan0)._kalinkaplayer._tcp.local.",
    ]


def test_txt_groups_instances_by_server_id_and_display_name(two_interfaces):
    discovery = ServiceDiscovery(KalinkaConfig())

    for announcement in discovery.announcements:
        properties = announcement.service_info.decoded_properties
        assert properties["server_id"] == SERVER_ID
        assert properties["display_name"] == "My Kalinka Service"
        assert properties["renderer_proto"] is not None


def test_configured_interface_announces_only_there(two_interfaces):
    config = KalinkaConfig()
    config.server.interface = "wlan0"

    discovery = ServiceDiscovery(config)

    assert [a.interface for a in discovery.announcements] == ["wlan0"]
    assert addresses_of(discovery.announcements[0].service_info) == [
        "10.20.0.15"
    ]
    assert discovery.announcements[0].service_info.name.startswith(
        "My Kalinka Service (wlan0)."
    )


def test_unknown_configured_interface_falls_back_to_all(two_interfaces):
    config = KalinkaConfig()
    config.server.interface = "tun9"

    discovery = ServiceDiscovery(config)

    assert [a.interface for a in discovery.announcements] == ["eth0", "wlan0"]


def test_no_interfaces_announces_nothing(monkeypatch):
    monkeypatch.setattr(
        service_discovery, "get_interface_ip_mappings", lambda: {}
    )

    assert ServiceDiscovery(KalinkaConfig()).announcements == []


def test_service_info_port_follows_config():
    config = KalinkaConfig()
    config.server.port = 9123

    info = get_service_info(config, "eth0", "192.168.1.20")

    assert info.port == 9123


class FakeAsyncZeroconf:
    def __init__(self, interfaces, fail_register):
        self.interfaces = interfaces
        self.fail_register = fail_register
        self.registered = []
        self.closed = False

    async def async_register_service(self, info):
        if self.fail_register:
            raise RuntimeError("bind failed")
        self.registered.append(info)

    async def async_close(self):
        self.closed = True


def install_fake_zeroconf(monkeypatch, fail_register=()):
    created = []

    def factory(*, ip_version, interfaces):
        fake = FakeAsyncZeroconf(interfaces, interfaces[0] in fail_register)
        created.append(fake)
        return fake

    monkeypatch.setattr(service_discovery, "AsyncZeroconf", factory)
    return created


async def test_register_binds_one_responder_per_interface(
    two_interfaces, monkeypatch
):
    created = install_fake_zeroconf(monkeypatch)
    discovery = ServiceDiscovery(KalinkaConfig())

    await discovery.register_service()

    assert [f.interfaces for f in created] == [
        ["192.168.1.20"],
        ["10.20.0.15"],
    ]
    assert all(len(f.registered) == 1 for f in created)


async def test_registration_failure_closes_its_responder_and_spares_the_rest(
    two_interfaces, monkeypatch
):
    created = install_fake_zeroconf(
        monkeypatch, fail_register={"192.168.1.20"}
    )
    discovery = ServiceDiscovery(KalinkaConfig())

    await discovery.register_service()

    assert created[0].closed
    assert discovery.announcements[0].zeroconf is None
    assert discovery.announcements[1].zeroconf is created[1]
    assert not created[1].closed


async def test_unregister_closes_every_responder(two_interfaces, monkeypatch):
    created = install_fake_zeroconf(monkeypatch)
    discovery = ServiceDiscovery(KalinkaConfig())

    await discovery.register_service()
    await discovery.unregister_service()

    assert all(f.closed for f in created)
    assert all(a.zeroconf is None for a in discovery.announcements)
