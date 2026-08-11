import socket
import fcntl
import struct
import netifaces


def default_route_ip() -> str:
    """
    The address of this machine another host on the LAN would reach it by.
    Found without sending traffic — a UDP socket "connect" only selects the
    route. Falls back to loopback when there is no route out.
    Returns:
        The IP address in quad-dotted notation.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def get_all_ip_addresses() -> list[str]:
    """
    Get all available IP addresses from all network interfaces.
    Excludes loopback addresses (127.x.x.x).
    Returns:
        List of IP addresses in quad-dotted notation.
    """
    ip_addresses = []

    for interface in netifaces.interfaces():
        try:
            # Skip loopback interface
            if interface == "lo":
                continue

            addrs = netifaces.ifaddresses(interface)
            # Check if interface has IPv4 addresses
            if netifaces.AF_INET in addrs:
                for addr_info in addrs[netifaces.AF_INET]:
                    ip = addr_info.get("addr")
                    if ip and not ip.startswith("127."):
                        ip_addresses.append(ip)
        except (KeyError, ValueError):
            # Skip interfaces that don't have proper addressing
            continue

    return ip_addresses


def get_ip_address(interface: str) -> str:
    """
    Uses the Linux SIOCGIFADDR ioctl to find the IP address associated
    with a network interface, given the name of that interface, e.g.
    "eth0". Only works on GNU/Linux distributions.
    Source: https://bit.ly/3dROGBN
    Returns:
        The IP address in quad-dotted notation of four decimal integers.
    """

    if interface == "all":
        return "0.0.0.0"

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    packed_iface = struct.pack("256s", interface.encode("utf_8"))
    packed_addr = fcntl.ioctl(sock.fileno(), 0x8915, packed_iface)[20:24]
    return socket.inet_ntoa(packed_addr)
