#!/usr/bin/env python3
"""Merge osv-scanner + trivy raw output into one normalized findings.json and a
self-contained dashboard.html (from scripts/vuln_dashboard.template.html).

Status model
  open     present in this scan (fix_available says whether a patched version exists)
  fixed    present in the baseline / previous scan, gone now
  new      open now and absent from the previous scan
"""
import glob
import json
import os
import sys
from datetime import datetime, timezone

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "reports/vuln")
RAW = os.path.join(OUT, "raw")
HERE = os.path.dirname(os.path.abspath(__file__))
SEV = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def clip(s, n=500):
    s = (s or "").strip().replace("\r", "")
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def norm_sev(s):
    s = (s or "UNKNOWN").upper()
    return {"MODERATE": "MEDIUM", "IMPORTANT": "HIGH", "NEGLIGIBLE": "LOW"}.get(s, s) if s not in SEV else s


def sev_from_score(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "UNKNOWN"
    return "CRITICAL" if x >= 9 else "HIGH" if x >= 7 else "MEDIUM" if x >= 4 else "LOW" if x > 0 else "UNKNOWN"


def trivy_score(v):
    best = None
    for src in (v.get("CVSS") or {}).values():
        s = src.get("V3Score") or src.get("V2Score")
        if s is not None and (best is None or s > best):
            best = s
    return best


def category(cls, typ, target):
    if cls == "os-pkgs":
        return "OS package"
    if cls == "lang-pkgs":
        return {"npm": "npm", "python-pkg": "Python", "pip": "Python", "gobinary": "Go binary"}.get(typ, typ or "library")
    if cls == "secret":
        return "Secret"
    if cls == "config":
        return "Misconfiguration"
    return cls


def short_target(t):
    # osv paths are absolute / temp dirs; keep from wcc-frontend/... on
    for marker in ("wcc-frontend/", "autosys/"):
        if marker in t:
            return t[t.index(marker):]
    return t


findings = {}  # key -> finding


def add(f):
    """Insert, merging duplicates reported by both tools (same place + package + shared id/alias)."""
    ids = {f["vuln_id"], *f["aliases"]}
    for k, ex in findings.items():
        if (ex["scope"], ex["target"], ex["package"], ex["installed"]) == (f["scope"], f["target"], f["package"], f["installed"]) \
                and ids & {ex["vuln_id"], *ex["aliases"]}:
            ex["tools"] = sorted(set(ex["tools"]) | set(f["tools"]))
            ex["aliases"] = sorted(set(ex["aliases"]) | ids - {ex["vuln_id"]})
            if not ex["fixed_version"] and f["fixed_version"]:
                ex["fixed_version"] = f["fixed_version"]
            if not ex["description"]:
                ex["description"] = f["description"]
            if ex["cvss"] is None:
                ex["cvss"] = f["cvss"]
            if ex["severity"] == "UNKNOWN":
                ex["severity"] = f["severity"]
            return
    key = f"{f['scope']}|{f['target']}|{f['package']}@{f['installed']}|{f['vuln_id']}"
    f["key"] = key
    findings[key] = f


def base(**kw):
    d = dict(scope="", target="", component="", category="", package="", installed="", fixed_version="",
             vuln_id="", aliases=[], title="", description="", severity="UNKNOWN", cvss=None, url="",
             cwe=[], tools=[], status="open", fix_note="", first_seen=None)
    d.update(kw)
    return d


# ---------------------------------------------------------------- osv-scanner
def osv_findings(path, scope="codebase", status="open"):
    out = []
    data = load(path)
    for res in (data or {}).get("results", []):
        target = short_target(res["source"]["path"])
        for p in res.get("packages", []):
            pkg = p["package"]
            groups = {i: g for g in p.get("groups", []) for i in g["ids"]}
            seen = set()
            for v in p.get("vulnerabilities", []):
                g = groups.get(v["id"])
                if g:
                    if g["ids"][0] in seen:
                        continue  # one finding per alias group
                    seen.add(g["ids"][0])
                fixed = ""
                for a in v.get("affected", []):
                    if a.get("package", {}).get("name") != pkg["name"]:
                        continue
                    for r in a.get("ranges", []):
                        fx = [e["fixed"] for e in r.get("events", []) if "fixed" in e]
                        if fx:
                            fixed = fx[-1]
                ds = v.get("database_specific", {})
                score = (g or {}).get("max_severity")
                sev = norm_sev(ds["severity"]) if ds.get("severity") else sev_from_score(score)
                aliases = sorted(set(v.get("aliases", [])) | set(g["ids"] if g else []) | set(g["aliases"] if g else []) - {v["id"]})
                url = next((r["url"] for r in v.get("references", []) if r.get("type") == "ADVISORY"), "") or f"https://osv.dev/{v['id']}"
                out.append(base(scope=scope, target=target, component=target, category=pkg["ecosystem"],
                                package=pkg["name"], installed=pkg["version"], fixed_version=fixed,
                                vuln_id=v["id"], aliases=[a for a in aliases if a != v["id"]],
                                title=v.get("summary", ""), description=clip(v.get("details")),
                                severity=sev, cvss=float(score) if score else None, url=url,
                                cwe=ds.get("cwe_ids", []), tools=["osv-scanner"], status=status))
    return out


# ---------------------------------------------------------------- trivy
def trivy_findings(path, scope):
    data = load(path)
    if not data:
        return []
    out = []
    image = data.get("ArtifactName", "")
    for res in data.get("Results", []):
        cls, typ, tgt = res["Class"], res.get("Type"), res["Target"]
        cat = category(cls, typ, tgt)
        if scope == "image":
            component = image if cls == "os-pkgs" else tgt
            target = image
        else:
            component = target = tgt
        for v in res.get("Vulnerabilities") or []:
            fixed = v.get("FixedVersion", "")
            note = "" if fixed else (v.get("Status") or "").replace("_", " ")
            out.append(base(scope=scope, target=target, component=component, category=cat,
                            package=v["PkgName"], installed=v.get("InstalledVersion", ""), fixed_version=fixed,
                            vuln_id=v["VulnerabilityID"], title=v.get("Title", ""), description=clip(v.get("Description")),
                            severity=norm_sev(v.get("Severity")), cvss=trivy_score(v), url=v.get("PrimaryURL", ""),
                            cwe=v.get("CweIDs", []), tools=["trivy"], fix_note=note))
        for m in res.get("Misconfigurations") or []:
            if m.get("Status") == "PASS":
                continue
            out.append(base(scope=scope, target=target, component=tgt, category=cat, package=m.get("Type", ""),
                            vuln_id=m["ID"], title=m.get("Title", ""), description=clip(m.get("Description") + " " + m.get("Resolution", "")),
                            severity=norm_sev(m.get("Severity")), url=m.get("PrimaryURL", ""), tools=["trivy"],
                            fix_note=clip(m.get("Resolution", ""), 200)))
        for s in res.get("Secrets") or []:
            out.append(base(scope=scope, target=target, component=tgt, category=cat, package=s.get("RuleID", ""),
                            vuln_id=f"secret:{s['RuleID']}", title=s.get("Title", ""),
                            description=clip(f"{s.get('Title')} detected at {tgt}:{s.get('StartLine')}. "
                                             "Debian's ssl-cert package ships a throwaway self-signed 'snakeoil' key; "
                                             "usually not a real secret." if "snakeoil" in tgt else f"Matched at {tgt}:{s.get('StartLine')}"),
                            severity=norm_sev(s.get("Severity")), tools=["trivy"]))
    return out


for f in osv_findings(os.path.join(RAW, "osv-source.json")):
    add(f)
for f in trivy_findings(os.path.join(RAW, "trivy-fs.json"), "codebase"):
    add(f)
for p in sorted(glob.glob(os.path.join(RAW, "trivy-image-*.json"))):
    for f in trivy_findings(p, "image"):
        add(f)

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ---------------------------------------------------------------- history / fixed
prev = load(os.path.join(OUT, "findings.json")) or {}
# Renamed scan targets: the same image under its old name must not read as "fixed".
TARGET_ALIAS = {"postgres:16": "autosys-postgres:16"}
for _f in prev.get("findings", []):
    if _f["target"] in TARGET_ALIAS:
        new = TARGET_ALIAS[_f["target"]]
        _f["key"] = _f["key"].replace(_f["target"], new, 1)
        _f["target"] = new
        if _f["component"] in TARGET_ALIAS:
            _f["component"] = new
prev_open = {f["key"]: f for f in prev.get("findings", []) if f["status"] != "fixed"}
carried_fixed = {f["key"]: f for f in prev.get("findings", []) if f["status"] == "fixed"}
first_seen = {f["key"]: f.get("first_seen") for f in prev.get("findings", [])}

for k, f in findings.items():
    f["first_seen"] = first_seen.get(k) or now
    if prev_open and k not in prev_open and k not in carried_fixed:
        f["status"] = "new"

# a finding that reappears is open again, not fixed
fixed = {k: f for k, f in carried_fixed.items() if k not in findings and f["category"] != "Secret"}
for k, f in prev_open.items():
    # secret scans are not deterministic across runs, so a missing secret is not proof it was removed
    if k not in findings and f["category"] != "Secret":
        f["status"], f["fixed_on"] = "fixed", now
        f["fix_note"] = "gone since previous scan"
        fixed[k] = f

# Baseline (older lockfile): anything vulnerable there that's no longer vulnerable here.
baseline = osv_findings(os.path.join(RAW, "osv-baseline.json"), status="fixed")
cur_by_pkg = {}
for f in findings.values():
    cur_by_pkg.setdefault((f["target"], f["package"]), []).append(f)
cur_versions = {}
for res in (load(os.path.join(RAW, "osv-source.json")) or {}).get("results", []):
    for p in res["packages"]:
        cur_versions[(short_target(res["source"]["path"]), p["package"]["name"])] = p["package"]["version"]
# Packages that vanished from the lockfile entirely are not in osv output; read lockfile for versions.
lock = load(os.path.join(HERE, "..", "wcc-frontend", "package-lock.json")) or {}
for path, meta in lock.get("packages", {}).items():
    if path.startswith("node_modules/"):
        cur_versions.setdefault(("wcc-frontend/package-lock.json", path.rsplit("node_modules/", 1)[1]), meta.get("version"))
for f in baseline:
    ids = {f["vuln_id"], *f["aliases"]}
    still = any(ids & {c["vuln_id"], *c["aliases"]} for c in cur_by_pkg.get((f["target"], f["package"]), []))
    if still:
        continue
    now_ver = cur_versions.get(("wcc-frontend/package-lock.json", f["package"]))
    f["key"] = f"{f['scope']}|{f['target']}|{f['package']}@{f['installed']}|{f['vuln_id']}"
    f["target"] = f["component"] = "wcc-frontend/package-lock.json"
    f["fix_note"] = f"upgraded {f['installed']} → {now_ver}" if now_ver and now_ver != f["installed"] else "no longer affected"
    f["fixed_on"] = "baseline"
    f["first_seen"] = None
    fixed.setdefault(f["key"], f)

all_f = list(findings.values()) + list(fixed.values())
all_f.sort(key=lambda f: (f["status"] == "fixed", SEV.index(f["severity"]), -(f["cvss"] or 0), f["package"]))
for i, f in enumerate(all_f):
    f["id"] = i

doc = {"generated": now, "baseline_note": os.environ.get("BASELINE_REF", ""), "findings": all_f}
os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "findings.json"), "w") as fh:
    json.dump(doc, fh, indent=1)

tpl = os.path.join(HERE, "vuln_dashboard.template.html")
if os.path.exists(tpl):
    html = open(tpl).read().replace("/*__DATA__*/null", json.dumps(doc, separators=(",", ":")).replace("</", "<\\/"))
    with open(os.path.join(OUT, "dashboard.html"), "w") as fh:
        fh.write(html)

op = [f for f in all_f if f["status"] != "fixed"]
print(f"{len(op)} open, {len(all_f) - len(op)} fixed")
for s in SEV:
    print(f"  {s:9} {sum(f['severity'] == s for f in op)}")
