"""Parse bulk proxy node input (one node per line)."""

from __future__ import annotations

from typing import Any


def _looks_like_proxy(value: str) -> bool:
    text = value.strip()
    if not text or any(ch.isspace() for ch in text):
        return False
    if "://" in text:
        scheme = text.split("://", 1)[0].lower()
        return scheme in {"http", "https", "socks", "socks5", "socks5h"}
    host, sep, port = text.rpartition(":")
    return bool(sep and host and port.isdigit())


def parse_node_lines(text: str) -> list[dict[str, Any]]:
    """Parse lines like ``url[,label[,weight[,group]]]`` or space separated."""
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split(",")] if "," in line else line.split()
        if len(parts) < 1 or not _looks_like_proxy(parts[0]):
            continue
        url = parts[0]
        if url in seen:
            continue
        seen.add(url)
        node: dict[str, Any] = {"url": url, "label": "", "weight": 1.0, "group": "", "enabled": True}
        if len(parts) > 1:
            node["label"] = parts[1]
        if len(parts) > 2:
            try:
                node["weight"] = max(0.0, float(parts[2]))
            except ValueError:
                pass
        if len(parts) > 3:
            node["group"] = parts[3]
        nodes.append(node)
    return nodes
