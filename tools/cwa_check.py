#!/usr/bin/env python3
"""CWA（中央氣象署）vs JMA（日本氣象廳）颱風路徑比對。

用法： python3 tools/cwa_check.py
需要： 同層或上層 .env 內含 CWB_API="CWA-xxxx..."（已被 .gitignore 排除）
"""
import json, math, os, re, sys, urllib.request, datetime

TPE = (25.0777, 121.2328)   # 桃園機場
PUS = (35.1796, 129.0756)   # 釜山
TRIP = ("2026-08-30", "2026-09-05")


def load_key():
    for p in (".env", "../.env"):
        if os.path.exists(p):
            m = re.search(r'^CWB_API=["\']?([^"\'\n]+)', open(p, encoding="utf-8").read(), re.M)
            if m and m.group(1).strip():
                return m.group(1).strip()
    sys.exit("找不到 .env 或 CWB_API 為空")


def dist(a, b):
    R, r = 6371, math.radians
    dla, dlo = r(b[0] - a[0]), r(b[1] - a[1])
    h = math.sin(dla / 2) ** 2 + math.cos(r(a[0])) * math.cos(r(b[0])) * math.sin(dlo / 2) ** 2
    return round(2 * R * math.asin(math.sqrt(h)))


def cwa_tracks(key):
    req = urllib.request.Request(
        "https://opendata.cwa.gov.tw/api/v1/rest/datastore/W-C0034-005?format=JSON",
        headers={"Authorization": key})
    j = json.load(urllib.request.urlopen(req, timeout=30))
    out = {}
    for t in j["records"]["TropicalCyclones"]["TropicalCyclone"]:
        en = (t.get("TyphoonName") or "").upper() or "TD%s" % t.get("CwaTdNo", "?")
        zh = t.get("CwaTyphoonName") or ""
        pts = []
        for x in t.get("AnalysisData", {}).get("Fix", [])[-1:]:
            pts.append((x["DateTime"][:16].replace("T", " "),
                        float(x["CoordinateLatitude"]), float(x["CoordinateLongitude"]),
                        x["MaxWindSpeed"], x["Pressure"], None))
        for x in t.get("ForecastData", {}).get("Fix", []):
            vt = (datetime.datetime.fromisoformat(x["InitialTime"])
                  + datetime.timedelta(hours=int(x["ForecastHour"]))).strftime("%Y-%m-%d %H:%M")
            pts.append((vt, float(x["CoordinateLatitude"]), float(x["CoordinateLongitude"]),
                        x["MaxWindSpeed"], x["Pressure"], x.get("Radius70PercentProbability")))
        out[en] = {"zh": zh, "pts": pts}
    return out


def jma_tracks():
    g = lambda u: json.load(urllib.request.urlopen(u, timeout=30))
    out = {}
    for t in g("https://www.jma.go.jp/bosai/typhoon/data/targetTc.json"):
        sp = g("https://www.jma.go.jp/bosai/typhoon/data/%s/specifications.json" % t["tropicalCyclone"])
        ti = [x for x in sp if x.get("part") == "title"][0]
        en = (ti.get("name", {}).get("en") or t["tropicalCyclone"]).upper()
        pts = []
        for x in sp:
            pos = x.get("position", {}).get("deg")
            if not pos:
                continue
            pts.append((x["validtime"]["JST"][:16].replace("T", " "), pos[0], pos[1],
                        (x.get("maximumWind") or {}).get("sustained", {}).get("m/s", "--"),
                        x.get("pressure", "--"), None))
        out[en] = {"zh": "", "pts": pts}
    return out


def main():
    cwa, jma = cwa_tracks(load_key()), jma_tracks()
    _cwa_for_scen = cwa
    print("=" * 78)
    print("CWA vs JMA 颱風比對　產生時間", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("行程 %s ~ %s｜桃園機場、釜山" % TRIP)
    print("=" * 78)
    for name in sorted(set(cwa) | set(jma)):
        c, j = cwa.get(name), jma.get(name)
        zh = (c or {}).get("zh") or ""
        print("\n▶ %s %s   CWA:%s  JMA:%s" % (name, zh, "有" if c else "無", "有" if j else "無"))
        days = sorted({p[0][:10] for s in (c, j) if s for p in s["pts"]})
        print("   %-12s %-34s %-34s" % ("日期", "CWA 位置/強度/距台", "JMA 位置/強度/距台"))
        for d in days:
            def pick(s):
                if not s:
                    return "—"
                cand = [p for p in s["pts"] if p[0][:10] == d]
                if not cand:
                    return "—"
                p = cand[len(cand) // 2]
                return "%.1fN %.1fE %sm/s 台%dkm" % (p[1], p[2], p[3], dist(TPE, (p[1], p[2])))
            print("   %-12s %-34s %-34s" % (d[5:], pick(c), pick(j)))
        # 對行程的威脅
        for label, loc in (("桃園", TPE), ("釜山", PUS)):
            for src, s in (("CWA", c), ("JMA", j)):
                if not s:
                    continue
                near = [(dist(loc, (p[1], p[2])), p[0]) for p in s["pts"] if TRIP[0] <= p[0][:10] <= TRIP[1]]
                if near:
                    m = min(near)
                    print("   ⚠ %s/%s 行程期間最近 %dkm（%s）" % (src, label, m[0], m[1]))
    for nm in cwa:
        if cwa[nm]["pts"] and min(dist(TPE,(p[1],p[2])) for p in cwa[nm]["pts"]) < 800:
            slowdown_scenarios(cwa, nm)


def slowdown_scenarios(cwa, name="SAUDEL", target=None):
    """把 CWA 預報路徑的移速打折，看最接近目標點的時間會延後多少。

    颱風實際移速常偏離預報（近三年台灣周邊個案：最慢 -58%、最快 +231%），
    路徑對了不代表時間對了——這是判斷「出發日會不會被掃到」的關鍵。
    """
    import datetime as _dt
    target = target or TPE
    s = cwa.get(name.upper())
    if not s or len(s["pts"]) < 3:
        return
    pts = s["pts"]
    t0 = _dt.datetime.strptime(pts[0][0], "%Y-%m-%d %H:%M")
    hrs, cum = [0.0], [0.0]
    for i in range(1, len(pts)):
        ti = _dt.datetime.strptime(pts[i][0], "%Y-%m-%d %H:%M")
        hrs.append((ti - t0).total_seconds() / 3600)
        cum.append(cum[-1] + dist((pts[i-1][1], pts[i-1][2]), (pts[i][1], pts[i][2])))
    dists = [dist(target, (p[1], p[2])) for p in pts]
    k = dists.index(min(dists))
    print("\n" + "=" * 78)
    print("減速敏感度：%s 最接近台灣（%d km）的時間" % (name, dists[k]))
    print("=" * 78)
    print("  %-20s %-24s %s" % ("情境", "抵達最近點", "較原預報延後"))
    for f, lab in ((1.0, "CWA 原預報"), (0.85, "移速 85%"), (0.7, "移速 70%"),
                   (0.6, "移速 60%"), (0.5, "移速 50%（停滯級）")):
        t = sum((hrs[i] - hrs[i-1]) / f for i in range(1, k + 1))
        arr = t0 + _dt.timedelta(hours=t)
        print("  %-20s %-24s +%.1f 小時 (%.1f 天)"
              % (lab, arr.strftime("%m/%d %H:%M"), t - hrs[k], (t - hrs[k]) / 24))
    print("  ※ 出發 %s 16:40／返程 %s" % (TRIP[0][5:], TRIP[1][5:]))


if __name__ == "__main__":
    main()
