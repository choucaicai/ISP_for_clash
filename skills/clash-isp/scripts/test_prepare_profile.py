"""Isolated fixtures only; no network or user Clash configuration access."""

import copy
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

import yaml

from prepare_profile import PrepareError, Sources, prepare, update_script


NODE = os.environ.get("CLASH_ISP_NODE") or shutil.which("node")


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "data"
        (self.root / "profiles").mkdir(parents=True)
        self.dump("profiles.yaml", {
            "current": "sub-a",
            "items": [
                {"uid": "sub-a", "type": "remote", "name": "Same name",
                 "file": "base-a.yaml", "url": "https://example.invalid/private-token",
                 "option": {"proxies": "pa", "script": "sa", "rules": "ra", "groups": "ga"},
                 "selected": [{"name": "AI", "now": "Proxies"}]},
                {"uid": "sub-b", "type": "remote", "name": "Same name",
                 "file": "base-b.yaml", "option": {"proxies": "pb", "script": "sb"}},
                {"uid": "pa", "file": "custom-proxies.yaml", "type": "proxies"},
            ]})
        self.base = {
            "proxies": [{"name": "US Relay", "type": "socks5", "server": "192.0.2.1", "port": 1080}],
            "proxy-groups": [{"name": "AI", "type": "select", "proxies": ["Proxies", "DIRECT"]},
                             {"name": "Proxies", "type": "select", "proxies": ["US Relay"]}],
            "rules": ["MATCH,Proxies"]}
        self.dump("profiles/base-a.yaml", self.base)
        self.dump("profiles/custom-proxies.yaml", {"prepend": [], "append": [], "delete": []})
        self.dump("profiles/ga.yaml", {"prepend": [], "append": [], "delete": []})
        self.dump("profiles/ra.yaml", {"prepend": ["DOMAIN-SUFFIX,old.example,OldAI"],
                                      "append": ["IP-CIDR,198.51.100.0/24,OldAI,no-resolve"], "delete": []})
        self.script = 'var calls = 0;\nfunction main(config, profileName) { calls++; config.tag = profileName; return config; }\n'
        (self.root / "profiles/sa.js").write_text(self.script)
        self.request = {"group": "AI", "nodes": [
            {"name": "ISP US (relay)", "type": "socks5", "server": "198.51.100.10", "port": 12324,
             "username": "fixture-user", "password": "fixture-secret:a@#", "dialer_proxy": "US Relay"}],
            "domains": [{"type": "DOMAIN-SUFFIX", "value": "new.example"}], "redirect_from": ["OldAI"]}
        self.out = Path(self.temp.name) / "bundle"

    def dump(self, relative, data):
        (self.root / relative).write_text(yaml.safe_dump(data, sort_keys=False))

    def read(self, relative, root=None):
        return yaml.safe_load(((root or self.root) / relative).read_text())

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def run_prepare(self, request=None, out=None):
        return prepare(self.root, "sub-a", request or self.request, out or self.out)

    def test_preparation_is_read_only_and_private(self):
        before = self.snapshot()
        plan = self.run_prepare()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(plan["profile_uid"], "sub-a")
        self.assertTrue(plan["active"])
        self.assertNotIn("fixture-secret", json.dumps(plan))
        self.assertNotIn("private-token", json.dumps(plan))
        manifest = self.read("manifest.json", self.out)
        self.assertNotIn("profiles/base-a.yaml", [f["source"] for f in manifest["files"]])
        self.assertIn("profiles.yaml", manifest["watched_sources"])
        self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o700)
        for path in self.out.rglob("*"):
            if path.is_file():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        proxies = self.read("profiles/custom-proxies.yaml", self.out)["append"]
        self.assertEqual(proxies[0]["password"], "fixture-secret:a@#")
        self.assertEqual(proxies[0]["dialer-proxy"], "US Relay")
        rules = self.read("profiles/ra.yaml", self.out)
        self.assertIn("DOMAIN-SUFFIX,new.example,AI", rules["prepend"])
        self.assertIn("DOMAIN-SUFFIX,old.example,AI", rules["prepend"])
        self.assertEqual(rules["append"], ["IP-CIDR,198.51.100.0/24,AI,no-resolve"])
        self.assertEqual(plan["redirected_overlay_rules"], 2)

    def test_repeat_is_idempotent_and_additive(self):
        self.run_prepare()
        manifest = self.read("manifest.json", self.out)
        for item in manifest["files"]:
            shutil.copyfile(self.out / item["staged"], self.root / item["source"])
        out2 = Path(self.temp.name) / "bundle2"
        self.run_prepare(out=out2)
        for item in manifest["files"]:
            self.assertEqual((out2 / item["staged"]).read_bytes(),
                             (self.out / item["staged"]).read_bytes())
        request = copy.deepcopy(self.request)
        request["nodes"][0]["name"] = "Second ISP"
        request["nodes"][0]["server"] = "198.51.100.11"
        out3 = Path(self.temp.name) / "bundle3"
        result = self.run_prepare(request, out3)
        self.assertEqual(result["managed_options"], ["Second ISP", "ISP US (relay)"])
        self.assertEqual(len(self.read("profiles/custom-proxies.yaml", out3)["append"]), 2)

    def test_missing_uid_and_same_name_not_accepted(self):
        for uid in ("Same name", "missing"):
            with self.subTest(uid=uid), self.assertRaises(PrepareError):
                prepare(self.root, uid, self.request, self.out)
        self.assertFalse(self.out.exists())

    def test_unknown_relay_and_cycle_rejected(self):
        for relay in ("Missing Relay", "AI", "ISP US (relay)"):
            request = copy.deepcopy(self.request)
            request["nodes"][0]["dialer_proxy"] = relay
            with self.subTest(relay=relay), self.assertRaises(PrepareError):
                self.run_prepare(request)
        self.base["proxies"][0]["dialer-proxy"] = "AI"
        self.dump("profiles/base-a.yaml", self.base)
        with self.assertRaises(PrepareError):
            self.run_prepare()

    def test_existing_conflict_not_overwritten(self):
        node = copy.deepcopy(self.request["nodes"][0])
        node["dialer-proxy"] = node.pop("dialer_proxy")
        node["password"] = "different-fixture"
        self.dump("profiles/custom-proxies.yaml", {"prepend": [], "append": [node], "delete": []})
        before = self.snapshot()
        with self.assertRaises(PrepareError):
            self.run_prepare()
        self.assertEqual(self.snapshot(), before)

    def test_non_select_or_deleted_group_rejected(self):
        self.base["proxy-groups"][0]["type"] = "url-test"
        self.dump("profiles/base-a.yaml", self.base)
        with self.assertRaises(PrepareError):
            self.run_prepare()
        self.base["proxy-groups"][0]["type"] = "select"
        self.dump("profiles/base-a.yaml", self.base)
        self.dump("profiles/ga.yaml", {"prepend": [], "append": [], "delete": ["AI"]})
        with self.assertRaises(PrepareError):
            self.run_prepare()

    def test_shared_enhancement_rejected(self):
        metadata = self.read("profiles.yaml")
        metadata["items"][1]["option"]["script"] = "sa"
        self.dump("profiles.yaml", metadata)
        with self.assertRaises(PrepareError):
            self.run_prepare()

    def test_path_escape_and_existing_output_rejected(self):
        self.out.mkdir()
        with self.assertRaises(PrepareError):
            self.run_prepare()
        self.out.rmdir()
        metadata = self.read("profiles.yaml")
        metadata["items"][0]["file"] = "../../outside.yaml"
        self.dump("profiles.yaml", metadata)
        with self.assertRaises(PrepareError):
            self.run_prepare()
        with self.assertRaises(PrepareError):
            Sources(self.root).read("../outside.yaml")

    def test_explicit_new_group_and_direct_http_node(self):
        request = {"group": "Custom ISP", "create_group": True, "nodes": [
            {"name": "ISP direct", "type": "http", "server": "198.51.100.20", "port": 8080}]}
        plan = self.run_prepare(request)
        self.assertEqual(plan["group"], "Custom ISP")
        node = self.read("profiles/custom-proxies.yaml", self.out)["append"][0]
        self.assertEqual(node["type"], "http")
        self.assertNotIn("dialer-proxy", node)
        self.assertNotIn("tls", node)
        manifest = self.read("manifest.json", self.out)
        self.assertNotIn("profiles/ra.yaml", [i["source"] for i in manifest["files"]])

    def test_unrelated_nodes_and_delete_lists_preserved(self):
        unrelated = {"name": "Other ISP", "type": "http", "server": "198.51.100.30", "port": 3128}
        self.dump("profiles/custom-proxies.yaml", {"prepend": [unrelated], "append": [], "delete": ["Retired ISP"]})
        self.run_prepare()
        staged = self.read("profiles/custom-proxies.yaml", self.out)
        self.assertEqual(staged["prepend"], [unrelated])
        self.assertEqual(staged["delete"], ["Retired ISP"])

    def test_const_entrypoint_requires_manual_composition(self):
        (self.root / "profiles/sa.js").write_text("const main = config => config;\n")
        with self.assertRaises(PrepareError):
            self.run_prepare()
        self.assertFalse(self.out.exists())

    def test_invalid_protocol_port_and_complex_rule_rejected(self):
        for key, value in (("type", "socks"), ("port", 0), ("port", True), ("tls", "true")):
            request = copy.deepcopy(self.request)
            request["nodes"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(PrepareError):
                self.run_prepare(request)
        self.dump("profiles/ra.yaml", {"prepend": ["AND,((DOMAIN,a.example)),OldAI"], "append": [], "delete": []})
        with self.assertRaises(PrepareError):
            self.run_prepare()

    def test_duplicate_yaml_keys_rejected(self):
        (self.root / "profiles.yaml").write_text("current: a\ncurrent: b\nitems: []\n")
        with self.assertRaises(PrepareError):
            self.run_prepare()

    def test_list_does_not_print_subscription_secret(self):
        result = subprocess.run([os.sys.executable, str(Path(__file__).with_name("prepare_profile.py")),
                                 "--root", str(self.root), "--list"], capture_output=True, text=True, check=True)
        self.assertEqual(len(json.loads(result.stdout)), 2)
        self.assertNotIn("private-token", result.stdout)

    @unittest.skipUnless(NODE, "Set CLASH_ISP_NODE or install Node for JS behavioral tests")
    def test_generated_script_preserves_main_and_other_group(self):
        self.run_prepare()
        script = (self.out / "profiles/sa.js").read_text()
        runner = '''
const fs = require("fs");
const vm = require("vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const scope = {};
vm.runInNewContext(input.script, scope);
const once = scope.main(input.config, "fixture-profile");
const twice = scope.main(once, "fixture-profile");
process.stdout.write(JSON.stringify({config: twice, calls: scope.calls}));
'''
        result = subprocess.run([NODE, "-e", runner],
                                input=json.dumps({"script": script, "config": self.base}),
                                capture_output=True, text=True, check=True)
        value = json.loads(result.stdout)
        self.assertEqual(value["calls"], 2)
        self.assertEqual(value["config"]["tag"], "fixture-profile")
        groups = value["config"]["proxy-groups"]
        self.assertEqual(groups[0]["proxies"], ["ISP US (relay)", "Proxies", "DIRECT"])
        self.assertEqual(groups[1], self.base["proxy-groups"][1])

    @unittest.skipUnless(NODE, "Set CLASH_ISP_NODE or install Node for JS behavioral tests")
    def test_two_managed_groups_keep_both_behaviors(self):
        script, _ = update_script(self.script, "AI", ["ISP One"], False)
        script, _ = update_script(script, "New Group", ["ISP Two"], True)
        script, _ = update_script(script, "AI", ["ISP Three"], False)
        runner = 'const vm=require("vm");const fs=require("fs");const x=JSON.parse(fs.readFileSync(0,"utf8"));const s={};vm.runInNewContext(x.script,s);process.stdout.write(JSON.stringify(s.main(x.config,"test")));'
        result = subprocess.run([NODE, "-e", runner], input=json.dumps({"script": script, "config": self.base}),
                                capture_output=True, text=True, check=True)
        groups = {g["name"]: g for g in json.loads(result.stdout)["proxy-groups"]}
        self.assertEqual(groups["AI"]["proxies"][:2], ["ISP Three", "ISP One"])
        self.assertEqual(groups["New Group"]["proxies"], ["ISP Two"])


if __name__ == "__main__":
    unittest.main()
