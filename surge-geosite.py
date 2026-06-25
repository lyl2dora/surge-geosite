#!/usr/bin/env python3
"""
surge-geosite: convert v2fly/domain-list-community data into Surge rule files.

Mirrors sing-geosite's pipeline. The data source is the GitHub repo
v2fly/domain-list-community (fetched automatically when no local data dir is
given); no dlc.dat and no Go needed. Faithfully replicating:
  - domain-list-community: resolveList (include / @attr filter / & affiliation)
    + polishList (redundant-subdomain pruning);
  - sing-geosite: parse() @attr output sublists, filterTags() (!cn set-difference),
    mergeTags() (cn / merged geolocation-cn).

Output (no GitHub releases):
  - rule-set/geosite-<code>.list   Surge RULE-SET   (DOMAIN-SUFFIX,x / DOMAIN,x)
  - domain-set/geosite-<code>.list Surge DOMAIN-SET  (.x / x)

Differences from sing-box .srs (intentional):
  - RootDomain -> single DOMAIN-SUFFIX line (Surge suffix already covers the apex),
    not sing-box's apex + ".suffix" pair;
  - regexp entries are dropped (counted/reported); keyword would drop in DOMAIN-SET
    too, but the dataset currently has none.
"""
import io
import os
import shutil
import sys
import tarfile
import urllib.request

# The data source of truth is the GitHub repo (this is what auto-updates).
# With no data_dir argument the script fetches the latest upstream from GitHub;
# pass a local checkout path to use that instead. Output dirs are cwd-relative.
# Override via argv: surge-geosite.py [data_dir] [ruleset_dir] [domainset_dir]
DLC_REPO = "v2fly/domain-list-community"
DLC_BRANCH = "master"
RULESET_DIR = "rule-set"
DOMAINSET_DIR = "domain-set"

# rule types (mirror internal/dlc/dlc.go)
DOMAIN = "domain"
FULL = "full"
KEYWORD = "keyword"
REGEXP = "regexp"
INCLUDE = "include"


class Entry:
    __slots__ = ("type", "value", "attrs", "plain")

    def __init__(self, typ, value, attrs):
        self.type = typ
        self.value = value
        self.attrs = attrs  # sorted list
        if attrs:
            self.plain = typ + ":" + value + ":" + ",".join("@" + a for a in attrs)
        else:
            self.plain = typ + ":" + value


class Inclusion:
    __slots__ = ("source", "must", "ban")

    def __init__(self, source, must, ban):
        self.source = source
        self.must = must
        self.ban = ban


class ParsedList:
    __slots__ = ("name", "inclusions", "entries")

    def __init__(self, name):
        self.name = name
        self.inclusions = []
        self.entries = []


def entry_key(e):
    return (e.type, e.value)


# ---------------------------------------------------------------- parsing ----

def parse_entry(typ, rule):
    parts = rule.split()
    if not parts:
        raise ValueError("empty domain rule")
    if typ == REGEXP:
        value = parts[0]
    elif typ in (DOMAIN, FULL, KEYWORD):
        value = parts[0].lower()
    else:
        raise ValueError("unknown rule type: %r" % typ)
    attrs = []
    affs = []
    for part in parts[1:]:
        c = part[0]
        if c == "@":
            attrs.append(part[1:].lower())
        elif c == "&":
            affs.append(part[1:].upper())
        else:
            raise ValueError("unknown field: %r" % part)
    attrs.sort()
    return Entry(typ, value, attrs), affs


def parse_inclusion(rule):
    parts = rule.split()
    if not parts:
        raise ValueError("empty inclusion")
    source = parts[0].upper()
    must, ban = [], []
    for part in parts[1:]:
        if part[0] == "@":
            attr = part[1:].lower()
            if attr and attr[0] == "-":
                ban.append(attr[1:])
            else:
                must.append(attr)
        else:
            raise ValueError("bad inclusion field: %r" % part)
    return Inclusion(source, must, ban)


def load_data(pl_map, list_name, path):
    pl = pl_map.get(list_name)
    if pl is None:
        pl = ParsedList(list_name)
        pl_map[list_name] = pl
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if ":" in line:
                typ, rule = line.split(":", 1)
                typ = typ.lower()
            else:
                typ, rule = DOMAIN, line
            if typ == INCLUDE:
                pl.inclusions.append(parse_inclusion(rule))
            else:
                entry, affs = parse_entry(typ, rule)
                for aff in affs:
                    apl = pl_map.get(aff)
                    if apl is None:
                        apl = ParsedList(aff)
                        pl_map[aff] = apl
                    apl.entries.append(entry)
                pl.entries.append(entry)


# ------------------------------------------------------------- resolving ----

def match_attr_filters(entry, inc):
    if not entry.attrs:
        return len(inc.must) == 0
    for m in inc.must:
        if m not in entry.attrs:
            return False
    for b in inc.ban:
        if b in entry.attrs:
            return False
    return True


def polish_list(rough):
    final = []
    queuing = []
    domains = set()
    for entry in rough.values():
        t = entry.type
        if t == REGEXP or t == KEYWORD:
            final.append(entry)
        elif t == DOMAIN:
            domains.add(entry.value)
            (final if entry.attrs else queuing).append(entry)
        elif t == FULL:
            (final if entry.attrs else queuing).append(entry)
    for q in queuing:
        pd = ("." + q.value) if q.type == FULL else q.value
        redundant = False
        while True:
            idx = pd.find(".")
            if idx < 0:
                break
            pd = pd[idx + 1:]
            if pd in domains:
                redundant = True
                break
        if not redundant:
            final.append(q)
    final.sort(key=lambda e: e.plain)
    return final


def resolve_list(name, pl_map, final_map, in_progress):
    if name in final_map:
        return
    pl = pl_map.get(name)
    if pl is None:
        raise ValueError("list %r not found" % name)
    if name in in_progress:
        raise ValueError("circular inclusion in: %r" % name)
    in_progress.add(name)
    rough = {}
    for e in pl.entries:
        rough[e.plain] = e
    for inc in pl.inclusions:
        resolve_list(inc.source, pl_map, final_map, in_progress)
        full_inc = not inc.must and not inc.ban
        for ie in final_map[inc.source]:
            if full_inc or match_attr_filters(ie, inc):
                rough[ie.plain] = ie
    in_progress.discard(name)
    final_map[name] = polish_list(rough) if rough else []


# ---------------------------------------- sing-geosite derived categories ----

def derive_attr_cats(final_map):
    """Base lists + per-attribute sublists, keyed by lowercase code (= sing-geosite parse)."""
    cats = {name.lower(): list(entries) for name, entries in final_map.items()}
    for name, entries in final_map.items():
        base = name.lower()
        buckets = {}
        for e in entries:
            for a in e.attrs:
                buckets.setdefault(a, []).append(e)
        for a, es in buckets.items():
            cats[base + "@" + a] = es
    return cats


def filter_tags(cats):
    """Mirror sing-geosite filterTags: drop redundant X-Y@Y, and X -= X@!Y / X-!Y@Y."""
    codes = list(cats.keys())
    bad_pairs = []
    deleted = 0
    for code in codes:
        parts = code.split("@")
        if len(parts) != 2:
            continue
        left, attr = parts[0], parts[1]
        lp = left.split("-")
        last = lp[-1] if len(lp) > 1 else ""
        if last == "":
            last = left
        if last == attr:
            cats.pop(code, None)
            deleted += 1
            continue
        if "!" + last == attr or last == "!" + attr:
            bad_pairs.append((left, code))
    diffed = 0
    for base, bad in bad_pairs:
        bad_entries = cats.get(bad)
        if bad_entries is None:
            continue
        cats.pop(bad, None)
        badset = set(entry_key(e) for e in bad_entries)
        base_list = cats.get(base, [])
        cats[base] = [e for e in base_list if entry_key(e) not in badset]
        diffed += 1
    return deleted, diffed


def merge_tags(cats):
    """Mirror sing-geosite mergeTags: build merged geolocation-cn and cn."""
    codes = list(cats.keys())
    cn_codes = []
    for code in codes:
        parts = code.split("@")
        if len(parts) == 2 and parts[1] == "cn":
            left = parts[0]
            if left.startswith("category-") and not (left.endswith("-cn") or left.endswith("-!cn")):
                cn_codes.append(code)
    for code in codes:
        if code.startswith("category-") and code.endswith("-cn") and "@" not in code:
            cn_codes.append(code)
    merged = {}
    for e in cats.get("geolocation-cn", []):
        merged[entry_key(e)] = e
    for code in cn_codes:
        for e in cats.get(code, []):
            merged[entry_key(e)] = e
    merged_list = list(merged.values())
    cats["geolocation-cn"] = merged_list
    cats["cn"] = merged_list + [Entry(DOMAIN, "cn", [])]
    return len(cn_codes)


# --------------------------------------------------------------- writing ----

_SFX_ORDER = {"DOMAIN-SUFFIX": 0, "DOMAIN": 1, "DOMAIN-KEYWORD": 2}


def to_ruleset_lines(entries):
    lines = set()
    dropped = 0
    for e in entries:
        if e.type == DOMAIN:
            lines.add("DOMAIN-SUFFIX," + e.value)
        elif e.type == FULL:
            lines.add("DOMAIN," + e.value)
        elif e.type == KEYWORD:
            lines.add("DOMAIN-KEYWORD," + e.value)
        elif e.type == REGEXP:
            dropped += 1
    out = sorted(lines, key=lambda s: (_SFX_ORDER[s.split(",", 1)[0]], s.split(",", 1)[1]))
    return out, dropped


def to_domainset_lines(entries):
    lines = set()
    dropped_re = dropped_kw = 0
    for e in entries:
        if e.type == DOMAIN:
            lines.add("." + e.value)
        elif e.type == FULL:
            lines.add(e.value)
        elif e.type == REGEXP:
            dropped_re += 1
        elif e.type == KEYWORD:
            dropped_kw += 1
    out = sorted(lines, key=lambda s: s[1:] if s[0] == "." else s)
    return out, dropped_re, dropped_kw


def clean_dir(d):
    os.makedirs(d, exist_ok=True)
    for fn in os.listdir(d):
        if fn.startswith("geosite-") and fn.endswith(".list"):
            os.remove(os.path.join(d, fn))


def fetch_github_data(dest="dlc-src"):
    """Download the latest domain-list-community from GitHub; return its data/ path."""
    url = "https://codeload.github.com/%s/tar.gz/refs/heads/%s" % (DLC_REPO, DLC_BRANCH)
    sys.stderr.write("fetching %s@%s from GitHub ...\n" % (DLC_REPO, DLC_BRANCH))
    with urllib.request.urlopen(url, timeout=180) as resp:
        blob = resp.read()
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        try:
            tar.extractall(dest, filter="data")
        except TypeError:
            tar.extractall(dest)
    for entry in sorted(os.listdir(dest)):
        cand = os.path.join(dest, entry, "data")
        if os.path.isdir(cand):
            return cand
    raise RuntimeError("data/ directory not found in fetched archive")


def main():
    global RULESET_DIR, DOMAINSET_DIR
    data_dir = sys.argv[1] if len(sys.argv) > 1 else fetch_github_data()
    if len(sys.argv) > 2:
        RULESET_DIR = sys.argv[2]
    if len(sys.argv) > 3:
        DOMAINSET_DIR = sys.argv[3]

    pl_map = {}
    for root, _dirs, files in os.walk(data_dir):
        for fn in files:
            load_data(pl_map, fn.upper(), os.path.join(root, fn))

    final_map = {}
    in_progress = set()
    for name in list(pl_map.keys()):
        resolve_list(name, pl_map, final_map, in_progress)

    cats = derive_attr_cats(final_map)
    n_base = len(final_map)
    n_attr = len(cats) - n_base
    f_deleted, f_diffed = filter_tags(cats)
    n_cn_merged = merge_tags(cats)

    clean_dir(RULESET_DIR)
    clean_dir(DOMAINSET_DIR)

    rs_files = ds_files = 0
    rs_lines = ds_lines = 0
    tot_sfx = tot_dom = tot_kw = tot_dropped = 0
    for code in sorted(cats.keys()):
        entries = cats[code]
        rlines, dropped = to_ruleset_lines(entries)
        tot_dropped += dropped
        if rlines:
            rs_files += 1
            rs_lines += len(rlines)
            tot_sfx += sum(1 for l in rlines if l[0] == "D" and l.startswith("DOMAIN-SUFFIX,"))
            tot_dom += sum(1 for l in rlines if l.startswith("DOMAIN,"))
            tot_kw += sum(1 for l in rlines if l.startswith("DOMAIN-KEYWORD,"))
            with open(os.path.join(RULESET_DIR, "geosite-" + code + ".list"), "w",
                      encoding="utf-8", newline="\n") as f:
                f.write("# Surge RULE-SET: geosite-%s\n" % code)
                f.write("# Source: v2fly/domain-list-community\n")
                f.write("# Rules: %d" % len(rlines))
                if dropped:
                    f.write(" (regexp dropped %d)" % dropped)
                f.write("\n")
                f.write("\n".join(rlines))
                f.write("\n")
        dlines, dre, dkw = to_domainset_lines(entries)
        if dlines:
            ds_files += 1
            ds_lines += len(dlines)
            with open(os.path.join(DOMAINSET_DIR, "geosite-" + code + ".list"), "w",
                      encoding="utf-8", newline="\n") as f:
                f.write("# Surge DOMAIN-SET: geosite-%s\n" % code)
                f.write("# Source: v2fly/domain-list-community\n")
                f.write("# Domains: %d" % len(dlines))
                extra = []
                if dre:
                    extra.append("regexp dropped %d" % dre)
                if dkw:
                    extra.append("keyword dropped %d" % dkw)
                if extra:
                    f.write(" (" + ", ".join(extra) + ")")
                f.write("\n")
                f.write("\n".join(dlines))
                f.write("\n")

    print("=== surge-geosite done ===")
    print("base lists          :", n_base)
    print("@attr sublists      :", n_attr, "(filtered out:", f_deleted, ", !cn-diffed:", f_diffed, ")")
    print("cn categories merged:", n_cn_merged, "-> geolocation-cn/cn")
    print("RULE-SET  dir       :", RULESET_DIR, "->", rs_files, "files,", rs_lines, "lines")
    print("DOMAIN-SET dir      :", DOMAINSET_DIR, "->", ds_files, "files,", ds_lines, "lines")
    print("  RULE-SET DOMAIN-SUFFIX/DOMAIN/KEYWORD:", tot_sfx, "/", tot_dom, "/", tot_kw)
    print("regexp dropped total:", tot_dropped)
    gl = cats.get("geolocation-cn", [])
    cn = cats.get("cn", [])
    print("geolocation-cn size :", len(gl), " | cn size:", len(cn))


if __name__ == "__main__":
    sys.setrecursionlimit(10000)
    main()
