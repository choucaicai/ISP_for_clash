#!/usr/bin/env python3
"""Stage subscription-local Clash enhancements; never apply or contact proxies."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import yaml


class PrepareError(ValueError):
    pass


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise PrepareError("Duplicate YAML key; inspect the source privately.")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)


def text(value, field):
    if not isinstance(value, str) or not value or value != value.strip():
        raise PrepareError(field + " must be a nonempty, unpadded string.")
    if any(ord(c) < 32 for c in value):
        raise PrepareError(field + " contains control characters.")
    return value


def mapping(value, field):
    if not isinstance(value, dict):
        raise PrepareError(field + " must be an object.")
    return value


def sequence(value, field):
    if not isinstance(value, list):
        raise PrepareError(field + " must be an array.")
    return value


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Sources:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve(strict=True)
        self.hashes = {}

    def read(self, relative, as_yaml=True):
        relative = Path(relative)
        if relative.is_absolute() or ".." in relative.parts:
            raise PrepareError("Unsafe source file path.")
        path = (self.root / relative).resolve(strict=True)
        if self.root not in path.parents:
            raise PrepareError("Source file escapes the data directory.")
        data = path.read_bytes()
        self.hashes[relative.as_posix()] = sha(data)
        decoded = data.decode("utf-8")
        if not as_yaml:
            return decoded
        try:
            return yaml.load(decoded, Loader=UniqueLoader)
        except yaml.YAMLError:
            raise PrepareError("Invalid YAML; inspect the source privately.") from None


def items_from(metadata):
    items = sequence(mapping(metadata, "profiles metadata").get("items"), "items")
    for item in items:
        mapping(item, "profile item")
    return items


def resolve_overlay(items, uid, extension):
    text(uid, "overlay UID")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", uid):
        raise PrepareError("Unsupported overlay UID.")
    found = [i for i in items if i.get("uid") == uid]
    if len(found) > 1:
        raise PrepareError("Duplicate overlay UID.")
    filename = found[0].get("file") if found else uid + extension
    return profile_file(filename)


def profile_file(filename):
    text(filename, "profile file")
    if "/" in filename or "\\" in filename or filename in (".", ".."):
        raise PrepareError("Expected a filename inside profiles/.")
    return "profiles/" + filename


def overlay(value, label):
    value = mapping(value, label)
    if set(value) - {"prepend", "append", "delete"}:
        raise PrepareError("Unsupported " + label + " fields.")
    result = copy.deepcopy(value)
    for key in ("prepend", "append", "delete"):
        sequence(result.setdefault(key, []), label + "." + key)
    return result


def named(entries, label):
    result = {}
    for item in entries:
        mapping(item, label)
        name = text(item.get("name"), label + " name")
        if name in result:
            raise PrepareError("Duplicate " + label + " name.")
        result[name] = item
    return result


def normalize_node(node):
    node = mapping(node, "node")
    allowed = {"name", "type", "server", "port", "username",
               "password", "dialer_proxy", "tls"}
    if set(node) - allowed:
        raise PrepareError("Unsupported node field.")
    result = {k: text(node.get(k), "node " + k) for k in ("name", "type", "server")}
    if result["name"] in {"DIRECT", "REJECT", "GLOBAL", "PASS"}:
        raise PrepareError("Reserved proxy name.")
    if result["type"] not in {"socks5", "http"}:
        raise PrepareError("Explicit socks5 or http protocol required.")
    server = result["server"]
    if any(c.isspace() or c in "/@#\\" for c in server):
        raise PrepareError("Server must be a plain IP or hostname, not a URI.")
    port = node.get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise PrepareError("Port must be an integer from 1 to 65535.")
    result["port"] = port
    if ("username" in node) != ("password" in node):
        raise PrepareError("Supply both username and password, or neither.")
    for key in ("username", "password"):
        if key in node:
            if not isinstance(node[key], str) or not node[key] or "\x00" in node[key]:
                raise PrepareError("Credentials must be nonempty strings.")
            result[key] = node[key]
    if "tls" in node:
        if type(node["tls"]) is not bool:
            raise PrepareError("tls must be a boolean.")
        result["tls"] = node["tls"]
    if "dialer_proxy" in node:
        result["dialer-proxy"] = text(node["dialer_proxy"], "dialer_proxy")
    return result


def check_graph(proxies, groups, target, additions):
    edges = {n: [p["dialer-proxy"]] if p.get("dialer-proxy") else []
             for n, p in proxies.items()}
    for name, group in groups.items():
        options = sequence(group.get("proxies", []), "group proxies")
        edges[name] = [text(n, "group member") for n in options]
    edges[target] = list(dict.fromkeys(additions + edges.get(target, [])))
    visiting, visited = set(), set()

    def visit(name):
        if name in visiting:
            raise PrepareError("Proxy/group dependency cycle detected.")
        if name in visited:
            return
        visiting.add(name)
        for child in edges.get(name, []):
            visit(child)
        visiting.remove(name)
        visited.add(name)

    for name in additions:
        relay = proxies[name].get("dialer-proxy")
        # Concrete nodes avoid provider/filter ambiguity and selector feedback loops.
        if relay and relay not in proxies:
            raise PrepareError("Relay must be an exact existing concrete proxy node.")
        visit(name)
    visit(target)


def redirect_rule(rule, old, target):
    text(rule, "rule")
    if not old:
        return rule
    fields = rule.split(",")
    # Nested logical/sub-rules need a real rule parser, never a string replacement.
    if any(c in rule for c in "()") or fields[0].strip() in {"AND", "OR", "NOT", "SUB-RULE"}:
        if any(field.strip() in old for field in fields):
            raise PrepareError("Complex rule migration requires manual inspection.")
        return rule
    index = 1 if fields[0].strip() == "MATCH" else 2
    if len(fields) > index and fields[index].strip() in old:
        fields[index] = target
    return ",".join(fields)


def update_script(source, group, names, create):
    token = hashlib.sha256(group.encode()).hexdigest()[:16]
    begin = "// BEGIN clash-isp managed " + token
    end = "// END clash-isp managed " + token
    previous = []
    if source.count(begin) != source.count(end) or source.count(begin) > 1:
        raise PrepareError("Damaged managed script block.")
    if begin in source:
        start = source.index(begin)
        finish = source.index(end, start) + len(end)
        existing = source[start:finish]
        match = re.search(r"^// clash-isp-data: (.+)$", existing, re.M)
        if not match:
            raise PrepareError("Managed script metadata missing.")
        try:
            previous = json.loads(match.group(1))["nodes"]
        except (ValueError, KeyError, TypeError):
            raise PrepareError("Invalid managed script metadata.") from None
        sequence(previous, "managed nodes")
        for name in previous:
            text(name, "managed node")
        source = source[:start] + source[finish:].lstrip("\n")
    names = list(dict.fromkeys(names + previous))
    if not re.search(r"\bfunction\s+main\s*\(|\b(?:const|let|var)\s+main\s*=", source):
        raise PrepareError("Existing script has no supported main entrypoint.")
    if "\x00" in source:
        raise PrepareError("Invalid script encoding.")
    # Reassign the original callable, without parsing/replacing its body or globals.
    data = json.dumps({"group": group, "nodes": names}, ensure_ascii=True)
    group_json = json.dumps(group, ensure_ascii=True)
    names_json = json.dumps(names, ensure_ascii=True)
    block = f'''{begin}
// clash-isp-data: {data}
main = (function (original) {{
  return function (config, profileName) {{
    var result = original(config, profileName);
    if (!result || typeof result !== "object" || Array.isArray(result)) {{
      throw new Error("The original script must return a configuration object.");
    }}
    var groups = result["proxy-groups"] || [];
    var found = groups.filter(function (item) {{ return item.name === {group_json}; }});
    if (found.length > 1) throw new Error("Duplicate target group.");
    var group = found[0];
    if (!group) {{
      if (!{str(create).lower()}) throw new Error("Target group not found.");
      group = {{ name: {group_json}, type: "select", proxies: [] }};
      groups.push(group);
      result["proxy-groups"] = groups;
    }}
    if (group.type !== "select") throw new Error("Target group is not select.");
    group.proxies = {names_json}.concat(group.proxies || []).filter(
      function (name, index, all) {{ return all.indexOf(name) === index; }}
    );
    return result;
  }};
}})(main);
{end}
'''
    # A lexical const main cannot be reassigned safely.
    if re.search(r"\bconst\s+main\s*=", source):
        raise PrepareError("const main requires manual composition; nothing was applied.")
    return source.rstrip() + "\n\n" + block, names


def prepare(root, uid, request, out):
    sources = Sources(root)
    metadata = sources.read("profiles.yaml")
    items = items_from(metadata)
    matches = [p for p in items if p.get("uid") == uid]
    if len(matches) != 1 or matches[0].get("type") not in {"remote", "local"}:
        raise PrepareError("Choose one exact remote/local subscription UID.")
    profile = matches[0]
    base_path = profile_file(profile.get("file"))
    base = mapping(sources.read(base_path), "base configuration")
    options = mapping(profile.get("option"), "profile options")
    request = mapping(request, "request")
    if set(request) - {"group", "create_group", "nodes", "domains", "redirect_from"}:
        raise PrepareError("Unsupported request field.")
    group = text(request.get("group"), "group")
    if "," in group or group in {"DIRECT", "REJECT", "GLOBAL", "PASS"}:
        raise PrepareError("Invalid target group name.")
    create = request.get("create_group", False)
    if type(create) is not bool:
        raise PrepareError("create_group must be a boolean.")
    nodes = [normalize_node(n) for n in sequence(request.get("nodes"), "nodes")]
    if not nodes or len({n["name"] for n in nodes}) != len(nodes):
        raise PrepareError("Supply at least one node with unique names.")
    domains = sequence(request.get("domains", []), "domains")
    redirect = sequence(request.get("redirect_from", []), "redirect_from")
    for old in redirect:
        text(old, "old group")
    needed = {"proxies": ".yaml", "script": ".js"}
    if domains or redirect:
        needed["rules"] = ".yaml"
    paths, documents = {}, {}
    for kind, extension in needed.items():
        if not options.get(kind):
            raise PrepareError("Create the subscription's " + kind + " enhancement in the client first.")
        paths[kind] = resolve_overlay(items, options[kind], extension)
        if paths[kind] == base_path:
            raise PrepareError("Enhancement unexpectedly points at the base subscription.")
        documents[kind] = sources.read(paths[kind], as_yaml=kind != "script")
    if len(set(paths.values())) != len(paths):
        raise PrepareError("Enhancement files must be distinct.")
    for other in items:
        if other.get("uid") == uid or other.get("type") not in {"local", "remote"}:
            continue
        other_options = other.get("option") or {}
        if any(options[k] in other_options.values() for k in needed):
            raise PrepareError("Enhancement UID is shared with another subscription.")
    proxy_overlay = overlay(documents["proxies"], "proxy overlay")
    group_entries = sequence(base.get("proxy-groups", []), "base groups")
    if options.get("groups"):
        groups_path = resolve_overlay(items, options["groups"], ".yaml")
        group_overlay = overlay(sources.read(groups_path), "group overlay")
        group_entries = group_overlay["prepend"] + group_entries + group_overlay["append"]
        if group in group_overlay["delete"]:
            raise PrepareError("Target group is marked for deletion.")
    if options.get("merge"):
        merge_path = resolve_overlay(items, options["merge"], ".yaml")
        sources.read(merge_path, as_yaml=False)
    groups = named(group_entries, "group")
    if group not in groups and not create:
        raise PrepareError("Target group was not found statically; inspect script/global enhancements or request creation.")
    if group in groups and groups[group].get("type") != "select":
        raise PrepareError("Existing target group is not a manual select group.")
    existing = named(proxy_overlay["prepend"] + proxy_overlay["append"], "overlay node")
    base_nodes = named(sequence(base.get("proxies", []), "base proxies"), "base node")
    combined = dict(base_nodes)
    for name, value in existing.items():
        if name in combined:
            raise PrepareError("Base and overlay contain a duplicate node name.")
        combined[name] = value
    for node in nodes:
        name = node["name"]
        if name in groups or name == group:
            raise PrepareError("Proxy name conflicts with a group.")
        if name in proxy_overlay["delete"]:
            raise PrepareError("Requested proxy is marked for deletion.")
        if name in combined:
            current = combined[name]
            if any(current.get(k) != v for k, v in node.items()):
                raise PrepareError("Existing node differs; explicitly resolve the name/credential conflict.")
        else:
            proxy_overlay["append"].append(node)
            combined[name] = node
    new_names = [n["name"] for n in nodes]
    script, managed = update_script(documents["script"], group, new_names, create)
    if any(name not in combined for name in managed):
        raise PrepareError("An existing managed node is no longer defined; inspect before applying.")
    check_graph(combined, groups, group, managed)
    outputs = {paths["proxies"]: yaml.safe_dump(proxy_overlay, allow_unicode=True, sort_keys=False),
               paths["script"]: script}
    redirected = 0
    if "rules" in paths:
        rules = overlay(documents["rules"], "rule overlay")
        for key in ("prepend", "append"):
            updated = [redirect_rule(rule, redirect, group) for rule in rules[key]]
            redirected += sum(a != b for a, b in zip(rules[key], updated))
            rules[key] = updated
        additions = []
        for domain in domains:
            mapping(domain, "domain")
            if set(domain) != {"type", "value"} or domain["type"] not in {"DOMAIN", "DOMAIN-SUFFIX"}:
                raise PrepareError("Only explicit DOMAIN or DOMAIN-SUFFIX additions are supported.")
            value = text(domain["value"], "domain value")
            if any(c.isspace() or c in ",/:*()" for c in value):
                raise PrepareError("Invalid domain value.")
            additions.append(domain["type"] + "," + value + "," + group)
        rules["prepend"] = list(dict.fromkeys(additions + rules["prepend"]))
        outputs[paths["rules"]] = yaml.safe_dump(rules, allow_unicode=True, sort_keys=False)
    out = Path(out).expanduser().resolve()
    if out == sources.root or sources.root / "profiles" in out.parents:
        raise PrepareError("Staging output must not be inside the live profiles directory.")
    if out.exists():
        raise PrepareError("Output directory already exists; choose a new directory.")
    manifest = {"schema": 1, "profile_uid": uid, "watched_sources": sources.hashes,
                "files": [{"source": path, "staged": path,
                           "source_sha256": sources.hashes[path],
                           "staged_sha256": sha(content.encode())}
                          for path, content in outputs.items()]}
    plan = {"profile_uid": uid, "profile_name": profile.get("name"),
            "active": metadata.get("current") == uid, "group": group,
            "added_options": new_names, "managed_options": managed,
            "redirected_overlay_rules": redirected, "domain_additions": len(domains),
            "activation_required": True,
            "warning": "Offline preparation only; preserve selections and verify generated configuration and egress."}
    out.mkdir(mode=0o700)
    for relative, content in dict(outputs, **{
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        "plan.json": json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
    }).items():
        destination = out / relative
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as handle:
            handle.write(content)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--spec")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        if args.list:
            if args.profile or args.spec or args.out:
                raise PrepareError("--list cannot be combined with mutation preparation arguments.")
            metadata = Sources(args.root).read("profiles.yaml")
            result = [{k: i.get(k) for k in ("uid", "name", "type", "updated")} |
                      {"active": i.get("uid") == metadata.get("current")}
                      for i in items_from(metadata) if i.get("type") in {"local", "remote"}]
        else:
            if not all((args.profile, args.spec, args.out)):
                raise PrepareError("--profile, --spec, and --out are all required.")
            try:
                request = json.loads(Path(args.spec).expanduser().read_text(encoding="utf-8"))
            except ValueError:
                raise PrepareError("Invalid request JSON; inspect it privately.") from None
            result = prepare(args.root, args.profile, request, args.out)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (PrepareError, OSError, UnicodeError, TypeError) as exc:
        # Never echo YAML/JSON content or a credential-bearing exception.
        message = str(exc) if isinstance(exc, PrepareError) else type(exc).__name__ + "; inspect local inputs privately."
        print("Error: " + message, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
