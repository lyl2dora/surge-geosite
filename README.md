# surge-geosite

Surge rule-sets generated from [v2fly/domain-list-community](https://github.com/v2fly/domain-list-community),
following the [sing-geosite](https://github.com/SagerNet/sing-geosite) flow but emitting **Surge** format
instead of sing-box `.srs`. Updated daily via GitHub Actions; output is force-pushed to dedicated
branches (no GitHub Releases).

## Consume in Surge

Replace `<owner>/<repo>` with your repository.

- **RULE-SET**: `https://raw.githubusercontent.com/<owner>/<repo>/rule-set/geosite-<code>.list`
- **DOMAIN-SET**: `https://raw.githubusercontent.com/<owner>/<repo>/domain-set/geosite-<code>.list`

```
[Rule]
RULE-SET,https://raw.githubusercontent.com/<owner>/<repo>/rule-set/geosite-netflix.list,PROXY
DOMAIN-SET,https://raw.githubusercontent.com/<owner>/<repo>/domain-set/geosite-cn.list,DIRECT
```

`<code>` is the lowercased list name (e.g. `geolocation-cn`, `category-ads`, `netflix`, `google@ads`).
Use DOMAIN-SET for big lists (smaller/faster); use RULE-SET if you need keyword rules.

## Type mapping

| domain-list-community | Surge RULE-SET | Surge DOMAIN-SET |
| --- | --- | --- |
| `domain:` / bare (RootDomain) | `DOMAIN-SUFFIX,x` | `.x` |
| `full:` | `DOMAIN,x` | `x` |
| `keyword:` | `DOMAIN-KEYWORD,x` | — (dataset currently has none) |
| `regexp:` | dropped | dropped |

`DOMAIN-SUFFIX` / `.x` already match the apex, so a RootDomain collapses to a single line.
Surge has no domain-level regex, so `regexp:` entries are dropped (mostly `category-porn`; the rest
are CDN/cloud hostnames covered by separate CDN rules).

## Build locally

```
# Fetch the latest data straight from GitHub and build:
python surge-geosite.py

# ...or build from a local checkout/clone of domain-list-community:
python surge-geosite.py path/to/domain-list-community/data
```

The bare command pulls the data from `v2fly/domain-list-community` on GitHub, so
no local copy is needed. Outputs `rule-set/` and `domain-set/` (cwd-relative).
