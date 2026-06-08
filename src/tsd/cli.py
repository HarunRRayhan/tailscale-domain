from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_DEVICE_DOMAIN = os.environ.get("TSD_DEVICE_DOMAIN", "mx.ts.harun.dev")
DEFAULT_CONFIG = Path(os.environ.get("TSD_CONFIG", Path("/Users") / os.environ.get("USER", "rayhan") / ".config" / "tsd" / "routes.json"))
DEFAULT_CADDY = Path(os.environ.get("TSD_CADDY", Path("/Users") / os.environ.get("USER", "rayhan") / ".config" / "tsd" / "routes.caddy"))
DEFAULT_UPSTREAM_HOST = os.environ.get("TSD_UPSTREAM_HOST", "host.docker.internal")
DEFAULT_UPSTREAM_SCHEME = os.environ.get("TSD_UPSTREAM_SCHEME", "http")
CADDY_CONTAINER = os.environ.get("TSD_CADDY_CONTAINER", "instagram-slides-caddy")

HELP_TEXT = """tailscale-domain route helper

Quick usage:
  tsd init                prompt for the device domain and create config if needed
  tsd add                 prompt for key, port, reference directory and save it
  tsd add is 9010         add/update a route directly
  tsd rm                  show routes and prompt for one to remove
  tsd list                list routes with key, domain, port, workdir
  tsd apply               regenerate the Caddy snippet

Route shape:
  key:     the local route key you type into tsd
  domain:   the public hostname that Caddy matches
  port:     the local upstream port
  workdir:  the reference directory associated with the route
  path:     optional path prefix for the route
"""


@dataclass
class Route:
    key: str
    port: int
    workdir: str = ""
    path: str = ""

    @property
    def domain(self) -> str:
        name = self.key.rstrip(".")
        if name.endswith(DEFAULT_DEVICE_DOMAIN):
            return name
        if name.endswith(".mx"):
            return f"{name}.ts.harun.dev"
        if "." in name:
            return name
        return f"{name}.{DEFAULT_DEVICE_DOMAIN}"

    @property
    def display_path(self) -> str:
        return self.path or "-"


def normalize_name(name: str) -> str:
    name = name.strip().rstrip(".")
    if name.startswith("https://"):
        name = name[len("https://") :]
    if name.startswith("http://"):
        name = name[len("http://") :]
    if name.startswith("www."):
        name = name[4:]
    return name


def normalize_route(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        port = int(value["port"])
        workdir = str(value.get("workdir", ""))
        path = str(value.get("path", ""))
        if workdir:
            workdir = str(Path(workdir).expanduser())
        if path and not path.startswith("/"):
            path = "/" + path
        return {"port": port, "workdir": workdir, "path": path}
    return {"port": int(value), "workdir": "", "path": ""}


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"device_domain": DEFAULT_DEVICE_DOMAIN, "routes": {}}
    config = json.loads(path.read_text())
    config["device_domain"] = config.get("device_domain") or DEFAULT_DEVICE_DOMAIN
    routes = config.setdefault("routes", {})
    config["routes"] = {name: normalize_route(value) for name, value in routes.items()}
    return config


def save_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "route"


def prompt(message: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{message}{suffix}: ").strip()
    except EOFError:
        if default:
            return default
        raise SystemExit(f"{message}{suffix} required")
    return value or default


def ensure_config(config: dict[str, Any]) -> None:
    if not config.get("device_domain"):
        config["device_domain"] = prompt("device domain", DEFAULT_DEVICE_DOMAIN)


def route_entries(routes: dict[str, dict[str, Any]]) -> list[tuple[str, dict[str, Any], Route]]:
    entries: list[tuple[str, dict[str, Any], Route]] = []
    for key in sorted(routes):
        route_data = routes[key]
        route = Route(
            key=key,
            port=int(route_data["port"]),
            workdir=str(route_data.get("workdir", "")),
            path=str(route_data.get("path", "")),
        )
        entries.append((key, route_data, route))
    return entries


def find_route_match(routes: dict[str, dict[str, Any]], query: str) -> Optional[Tuple[str, dict[str, Any], Route]]:
    normalized = normalize_name(query)
    entries = route_entries(routes)
    exact = [item for item in entries if item[0] == normalized or item[2].domain == normalized]
    if exact:
        return exact[0]
    prefix = [item for item in entries if item[0].startswith(normalized) or item[2].domain.startswith(normalized)]
    if prefix:
        return prefix[0]
    contains = [item for item in entries if normalized in item[0] or normalized in item[2].domain]
    if contains:
        return contains[0]
    return None


def print_routes(routes: dict[str, dict[str, Any]], highlight: Optional[str] = None) -> None:
    entries = route_entries(routes)
    if not entries:
        print("no routes configured")
        return
    for idx, (key, _, route) in enumerate(entries, start=1):
        marker = "*" if highlight and (highlight == key or highlight == route.domain) else " "
        reference = route.workdir or "-"
        print(f"{marker} {idx}. {route.domain}")
        print(f"    key: {key}")
        print(f"    port: {route.port}")
        print(f"    reference: {reference}")
        print(f"    path: {route.display_path}")


def render_caddy(config: dict[str, Any]) -> str:
    routes: dict[str, dict[str, Any]] = config.get("routes", {})
    lines = ["# generated by tsd; do not edit"]
    for key in sorted(routes):
        route = Route(
            key=key,
            port=int(routes[key]["port"]),
            workdir=str(routes[key].get("workdir", "")),
            path=str(routes[key].get("path", "")),
        )
        host = route.domain
        matcher = f"tsd_{slugify(host)}_{hashlib.sha1(host.encode('utf-8')).hexdigest()[:8]}"
        lines.append(f"@{matcher} host {host}")
        lines.append(f"handle @{matcher} {{")
        if route.path:
            lines.append(f"\thandle_path {route.path}* {{")
            lines.append(f"\t\treverse_proxy {DEFAULT_UPSTREAM_HOST}:{route.port}")
            lines.append("\t}")
        else:
            lines.append(f"\treverse_proxy {DEFAULT_UPSTREAM_HOST}:{route.port}")
        lines.extend(["}", ""])
    if len(lines) == 1:
        lines.append("# no routes configured")
    return "\n".join(lines).rstrip() + "\n"


def restart_caddy() -> None:
    try:
        subprocess.run(
            ["docker", "restart", CADDY_CONTAINER],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        return
    except subprocess.CalledProcessError as exc:
        print(exc.stderr.strip() or exc.stdout.strip(), file=sys.stderr)


def write_outputs(config: dict[str, Any]) -> None:
    save_config(DEFAULT_CONFIG, config)
    DEFAULT_CADDY.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_CADDY.write_text(render_caddy(config))
    restart_caddy()


def cmd_init(_: argparse.Namespace) -> int:
    config = load_config(DEFAULT_CONFIG)
    if DEFAULT_CONFIG.exists():
        print(f"config already exists: {DEFAULT_CONFIG}")
        print(f"device_domain={config.get('device_domain', DEFAULT_DEVICE_DOMAIN)}")
        return 0
    config["device_domain"] = prompt("device domain", DEFAULT_DEVICE_DOMAIN)
    config["routes"] = {}
    write_outputs(config)
    print(f"initialized {DEFAULT_CONFIG}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    config = load_config(DEFAULT_CONFIG)
    ensure_config(config)
    routes = config.setdefault("routes", {})

    default_workdir = args.workdir if args.workdir else str(Path.cwd())
    workdir_input = prompt("reference directory", default_workdir)
    key = normalize_name(args.name or prompt("route key (e.g. is or mx.ts.harun.dev)"))
    port_text = str(args.port) if args.port is not None else prompt("local port")
    path_input = args.path if args.path is not None else prompt("path prefix (optional)", "")
    workdir = str(Path(workdir_input).expanduser().resolve())
    path = path_input.strip()
    if path and not path.startswith("/"):
        path = "/" + path

    existing = routes.get(key)
    if existing is not None:
        existing_route = normalize_route(existing)
        existing_obj = Route(
            key,
            int(existing_route["port"]),
            existing_route.get("workdir", ""),
            existing_route.get("path", ""),
        )
        print(
            f"existing route: key={key} domain={existing_obj.domain} port={existing_obj.port} workdir={existing_obj.workdir or '-'} path={existing_obj.display_path}"
        )
        overwrite = prompt("overwrite existing route? [y/N]", "n").lower()
        if overwrite not in {"y", "yes"}:
            print("aborted")
            return 0

    routes[key] = {"port": int(port_text), "workdir": workdir, "path": path}
    config["device_domain"] = config.get("device_domain") or DEFAULT_DEVICE_DOMAIN
    write_outputs(config)
    print(f"added {Route(key, int(port_text), workdir, path).domain} -> {port_text} ({workdir})")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    config = load_config(DEFAULT_CONFIG)
    routes = config.setdefault("routes", {})
    if not routes:
        print("no routes configured")
        return 0

    print("current routes:")
    default_key = route_entries(routes)[0][0]
    print_routes(routes, highlight=default_key)

    query = args.name or prompt("route key to remove", default_key)
    match = find_route_match(routes, query)
    if match is None:
        print(f"no route matched {query}")
        return 0

    key, _, route_obj = match
    print(f"selected: {route_obj.domain} -> {route_obj.port}")
    confirm = prompt(f"remove {route_obj.domain} -> {route_obj.port}? [y/N]", "n").lower()
    if confirm not in {"y", "yes"}:
        print("aborted")
        return 0

    routes.pop(key, None)
    write_outputs(config)
    print(f"removed {route_obj.domain}")
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    config = load_config(DEFAULT_CONFIG)
    routes: dict[str, dict[str, Any]] = config.get("routes", {})
    print_routes(routes)
    return 0


def cmd_apply(_: argparse.Namespace) -> int:
    config = load_config(DEFAULT_CONFIG)
    write_outputs(config)
    print(f"wrote {DEFAULT_CADDY}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tsd",
        description="tailscale-domain route helper",
        epilog=HELP_TEXT,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="create the config file if it does not already exist")
    init.set_defaults(func=cmd_init)

    add = sub.add_parser("add", help="add or update a route; prompts when fields are missing")
    add.add_argument("name", nargs="?", help="route key, e.g. is or mx.ts.harun.dev")
    add.add_argument("port", nargs="?", type=int, help="local upstream port")
    add.add_argument("--workdir", help="reference directory for the route", default="")
    add.add_argument("--path", help="optional path prefix, e.g. /instagram-slides", default=None)
    add.set_defaults(func=cmd_add)

    rm = sub.add_parser("remove", aliases=["rm"], help="remove a route; shows existing routes first")
    rm.add_argument("name", nargs="?", help="route key, e.g. is or is.mx.ts.harun.dev")
    rm.set_defaults(func=cmd_remove)

    ls = sub.add_parser("list", aliases=["ls"], help="list routes")
    ls.set_defaults(func=cmd_list)

    apply_cmd = sub.add_parser("apply", help="rewrite the generated Caddy snippet")
    apply_cmd.set_defaults(func=cmd_apply)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
