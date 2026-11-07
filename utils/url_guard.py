"""远程图片 URL 的 SSRF 防护。

背景
----
``/v1/images/edits``、``/v1/chat/completions`` 等接口允许传 ``image_url``，
服务端会去下载这个地址。如果不做校验，持有 API key 的调用方就能让服务端
去请求任意地址——包括本机回环、内网主机、云厂商元数据服务
（``169.254.169.254``）——这是典型的 SSRF。

这里在**发起请求前**校验目标主机：解析域名得到 IP，任一解析结果落在
私有/回环/链路本地/保留网段就拒绝。重定向也逐跳校验，避免用 302 跳到内网。

内网部署场景确实需要拉内网图片时，把配置项 ``allow_private_image_urls``
置为 true 即可放行。
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# 跟随重定向的最大跳数
MAX_REDIRECTS = 3


class BlockedImageUrlError(ValueError):
    """目标地址不允许被服务端访问。"""


def _is_blocked_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def is_blocked_host(host: str, allow_private: bool = False) -> bool:
    """主机名解析出的地址是否属于禁止访问的网段。

    解析失败时返回 False（放行）：解析不出来的主机本来就连不通，
    请求最终仍会失败，不构成 SSRF。真正要拦的是「解析成功但指向内网」。
    """
    host = (host or "").strip().strip("[]")
    if not host:
        return True
    if allow_private:
        return False
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    except OSError:
        return False
    for info in infos:
        address = info[4][0]
        if _is_blocked_address(str(address)):
            return True
    return False


def check_image_url(url: str, allow_private: bool = False) -> str:
    """校验图片 URL，合法则原样返回，非法抛 BlockedImageUrlError。"""
    source = (url or "").strip()
    parsed = urlparse(source)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BlockedImageUrlError("image_url must be an http or https URL")
    host = parsed.hostname or ""
    if is_blocked_host(host, allow_private=allow_private):
        raise BlockedImageUrlError(f"image_url host is not allowed: {host}")
    return source


def check_final_url(url: str, allow_private: bool = False) -> None:
    """复查请求最终落到的地址。

    服务端允许跟随重定向，攻击者可以先给一个公网地址、再 302 跳到内网。
    请求发出后拿最终 URL 再校验一次，堵掉这条绕过路径。
    """
    if allow_private or not url:
        return
    parsed = urlparse(str(url).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return
    host = parsed.hostname or ""
    if is_blocked_host(host, allow_private=allow_private):
        raise BlockedImageUrlError(f"image_url host is not allowed: {host}")
