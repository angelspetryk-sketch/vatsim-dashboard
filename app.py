from flask import Flask, render_template, request
import requests, warnings, time
from datetime import datetime, timedelta
from collections import defaultdict

warnings.filterwarnings("ignore", category=DeprecationWarning)
app = Flask(__name__)
CACHE = {}
CACHE_TTL = 300

def cache_get(k):
    e = CACHE.get(k)
    if not e: return None
    if time.time() - e["t"] > CACHE_TTL:
        del CACHE[k]; return None
    return e["data"]

def cache_set(k, d): CACHE[k] = {"t": time.time(), "data": d}

def fetch_pilot_name(cid):
    k = f"pilot:{cid}"
    c = cache_get(k)
    if c is not None: return c
    try:
        pr = requests.get(f"https://api.vatsim.net/api/ratings/{cid}/", headers={'Accept':'application/json'}, timeout=10)
        if pr.status_code == 200:
            j = pr.json(); name = j.get("name") or j.get("full_name")
            cache_set(k, name); return name
    except: pass
    cache_set(k, None); return None

def fetch_sessions(cid, start_date):
    k = f"sessions:{cid}:{start_date}"
    c = cache_get(k)
    if c is not None: return c
    r = requests.get(f"https://api.vatsim.net/api/ratings/{cid}/atcsessions/?start={start_date}", headers={'Accept':'application/json'}, timeout=15)
    if r.status_code != 200: return {"error": r.status_code, "results": []}
    d = {"error": None, "results": r.json().get("results", [])}
    cache_set(k, d); return d

def format_hhmm(m):
    t = int(round(m)); return f"{t//60:02d}:{t%60:02d}"

def calc_streaks(daily, today):
    active = sorted([d for d, m in daily.items() if m > 0])
    if not active: return {"current": 0, "best": 0, "longest_break": 0}
    best = cur = 1
    for i in range(1, len(active)):
        if (active[i] - active[i-1]).days == 1: cur += 1; best = max(best, cur)
        else: cur = 1
    aset = set(active); current = 0
    chk = today if today in aset else today - timedelta(days=1)
    while chk in aset: current += 1; chk -= timedelta(days=1)
    lb = 0
    for i in range(1, len(active)):
        g = (active[i] - active[i-1]).days - 1
        if g > lb: lb = g
    return {"current": current, "best": best, "longest_break": lb}

def build_heatmap(all_sessions):
    today = datetime.utcnow().date(); start = today - timedelta(days=364)
    daily = defaultdict(float)
    for s in all_sessions:
        d = s["start_dt"].date()
        if start <= d <= today: daily[d] += float(s.get("minutes_on_callsign", 0))
    max_min = max(daily.values()) if daily else 0
    grid_start = start - timedelta(days=start.weekday())
    weeks = []; current = grid_start; month_labels = []; last_month = None
    while current <= today:
        week = []; wf = None
        for _ in range(7):
            if start <= current <= today:
                m = daily.get(current, 0)
                if m == 0: lvl = 0
                else:
                    r = m / max_min if max_min else 0
                    lvl = 4 if r >= 0.75 else 3 if r >= 0.5 else 2 if r >= 0.25 else 1
                week.append({"date": current.strftime("%b %d, %Y"), "hhmm": format_hhmm(m), "minutes": m, "level": lvl, "in_range": True})
                if wf is None: wf = current
            else: week.append({"in_range": False, "level": -1})
            current += timedelta(days=1)
        if wf:
            mn = wf.strftime("%b")
            if mn != last_month:
                month_labels.append({"col": len(weeks), "label": mn}); last_month = mn
        weeks.append(week)
    tm = sum(daily.values()); ad = sum(1 for v in daily.values() if v > 0)
    st = calc_streaks(daily, today)
    return {"weeks": weeks, "month_labels": month_labels, "total_hhmm": format_hhmm(tm),
            "active_days": ad, "current_streak": st["current"], "best_streak": st["best"],
            "longest_break": st["longest_break"], "has_data": tm > 0}

@app.route("/", methods=["GET"])
def home():
    cid = request.args.get("cid", "1626475")
    sort_by = request.args.get("sort", "hours")
    mode = request.args.get("mode", "rolling")
    excluded = set(filter(None, request.args.get("exclude", "").split("|")))
    home_override = request.args.get("home", "").strip()
    pilot_name = fetch_pilot_name(cid)
    cy = datetime.now().year; cm = datetime.now().month
    available_periods = ["h1"]
    if cm >= 7: available_periods.append("h2")
    available_quarters = ["q1"]
    if cm >= 4: available_quarters.append("q2")
    if cm >= 7: available_quarters.append("q3")
    if cm >= 10: available_quarters.append("q4")
    available_modes = ["rolling"] + available_periods + available_quarters
    if mode not in available_modes: mode = "rolling"
    if mode == "h1": since, until = datetime(cy,1,1), datetime(cy,6,30,23,59,59)
    elif mode == "h2": since, until = datetime(cy,7,1), datetime(cy,12,31,23,59,59)
    elif mode == "q1": since, until = datetime(cy,1,1), datetime(cy,3,31,23,59,59)
    elif mode == "q2": since, until = datetime(cy,4,1), datetime(cy,6,30,23,59,59)
    elif mode == "q3": since, until = datetime(cy,7,1), datetime(cy,9,30,23,59,59)
    elif mode == "q4": since, until = datetime(cy,10,1), datetime(cy,12,31,23,59,59)
    else: since, until = datetime.now() - timedelta(weeks=13), datetime.now()

    now_dt = datetime.now()
    period_countdown = None
    if mode != "rolling":
        total_days = (until.date() - since.date()).days + 1
        days_left = max(0, (until.date() - now_dt.date()).days)
        days_elapsed = max(0, total_days - days_left)
        pct_elapsed = round(days_elapsed / total_days * 100, 1) if total_days else 0
        period_countdown = {
            "label": mode.upper(),
            "end_date": until.strftime("%b %d, %Y"),
            "days_left": days_left,
            "total_days": total_days,
            "days_elapsed": days_elapsed,
            "pct_elapsed": pct_elapsed,
            "is_over": now_dt > until,
        }

    start_date = (datetime.now() - timedelta(days=548)).strftime("%Y-%m-%d")
    sr = fetch_sessions(cid, start_date)
    if sr["error"]: return f"API Error: {sr['error']}"
    all_sessions = []
    for s in sr["results"]:
        s = dict(s); s["start_dt"] = datetime.fromisoformat(s["start"]); all_sessions.append(s)
    filtered_data = [s for s in all_sessions if since <= s["start_dt"] <= until]

    callsign_stats = defaultdict(lambda: {"minutes": 0, "sessions": 0})
    for s in filtered_data:
        cs = s.get("callsign", "UNKNOWN").upper()
        callsign_stats[cs]["minutes"] += float(s.get("minutes_on_callsign", 0))
        callsign_stats[cs]["sessions"] += 1
    all_callsigns = [{"callsign": c, "minutes": st["minutes"], "hours_hhmm": format_hhmm(st["minutes"]), "sessions": st["sessions"]} for c, st in callsign_stats.items()]
    all_callsigns.sort(key=lambda x: x["minutes"], reverse=True)
    top_callsigns = all_callsigns[:5]

    groups = {
        "VATSIM Germany": {"prefixes": ("ED","EDXX",), "min_hours": 3, "flags": ["de"]},
        "TRvACC": {"prefixes": ("ANK","IST","LT"), "min_hours": 3, "flags": ["tr"]},
        "VATSIM Egyptian vACC": {"prefixes": ("HE",), "min_hours": 3, "flags": ["eg"]},
        "Latvia vACC": {"prefixes": ("EV",), "min_hours": 3, "flags": ["lv"]},
        "Arabian vACC": {"prefixes": ("OO","OM"), "min_hours": 3, "flags": ["ae","om"]},
        "Saudi Arabia vACC": {"prefixes": ("OE",), "min_hours": 3, "flags": ["sa"]},
        "VACC Czechia": {"prefixes": ("LK",), "min_hours": 3, "flags": ["cz"]},
        "vACC Slovakia": {"prefixes": ("LZ",), "min_hours": 3, "flags": ["sk"]},
        "Maghreb vACC": {"prefixes": ("GM","DA","DT"), "min_hours": 3, "flags": ["ma","dz","tn"]},
        "Khaleej vACC": {"prefixes": ("OT","OB"), "min_hours": 3, "flags": ["qa","bh"]},
        "VATSSA": {"prefixes": ("FA","FI","FM","FQ","FV","FB","FY","FN","FL","FW","HT","HB","HR","FZ","FC","HK","DG","DB","DU","GK","GI","GV","DR","FT","FS"), "min_hours": 3, "flags": ["za","mu","mg","na","bw","zw","mz","ao","zm","mw","tz","bi","rw","ug","ke","sc","cd","gh","ng","ci","lr","sn","cv"]},
        "vZMA ARTCC": {"prefixes": ("TPA","RSW","PBI","MIA"), "min_hours": 3, "flags": ["us"]},
        "Atlanta ARTCC": {"prefixes": ("CLT","ATL"), "min_hours": 3, "flags": ["us"]},
        "VATSIM North East Africa": {"prefixes": ("HS","HJ","HA","HH","HC","HD"), "min_hours": 3, "flags": ["sd","ss","et","er","so","dj"]},
        "VATITA": {"prefixes": ("LI",), "min_hours": 3, "flags": ["it"]},
        "Polish vACC": {"prefixes": ("EP",), "min_hours": 3, "flags": ["pl"]},
        "Romanian vACC": {"prefixes": ("LR","LU"), "min_hours": 3, "flags": ["ro","md"]},
        "Bulgarian vACC": {"prefixes": ("LB",), "min_hours": 3, "flags": ["bg"]},
        "BELUX vACC": {"prefixes": ("EB","EL"), "min_hours": 3, "flags": ["be","lu"]},
        "Lithuania vACC": {"prefixes": ("EY",), "min_hours": 3, "flags": ["lt"]},
        "Iraq & Kuwait": {"prefixes": ("OK","OR"), "min_hours": 3, "flags": ["iq","kw"]},
        "VATAdria": {"prefixes": ("ADR","LJ","LQ","LD","LY","LW","LA"), "min_hours": 3, "flags": ["si","hr","ba","rs","al","mk"]},
        "vACC Hungary": {"prefixes": ("LH",), "min_hours": 3, "flags": ["hu"]},
        "vACC Austria": {"prefixes": ("LO",), "min_hours": 3, "flags": ["at"]},
        "Spain vACC": {"prefixes": ("LE",), "min_hours": 3, "flags": ["es"]},
        "VATSIM Scandinavia": {"prefixes": ("BI","EK","EN","EF","ES"), "min_hours": 3, "flags": ["is","dk","no","se","fi"]},
        "VATSIM UK": {"prefixes": ("EG","THAMES","LON","ESSEX"), "min_hours": 3, "flags": ["gb"]},
        "VATéir": {"prefixes": ("EI",), "min_hours": 3, "flags": ["ie"]},
        "French vACC": {"prefixes": ("LF",), "min_hours": 3, "flags": ["fr"]},
        "vACC Switzerland": {"prefixes": ("LS",), "min_hours": 3, "flags": ["ch"]},
        "Portugal vACC": {"prefixes": ("LP",), "min_hours": 3, "flags": ["pt"]},
        "Hellenic vACC": {"prefixes": ("LG",), "min_hours": 3, "flags": ["gr"]},
        "vACC Estonia": {"prefixes": ("EE",), "min_hours": 3, "flags": ["ee"]},
        "Dutch vACC": {"prefixes": ("EH",), "min_hours": 3, "flags": ["nl"]},
        "vACC Austria": {"prefixes": ("LO",), "min_hours": 3, "flags": ["at"]},
        "VATSIM Iran": {"prefixes": ("TEH","OI"), "min_hours": 3, "flags": ["ir"]},
        "Caucasus ACC": {"prefixes": ("UB","UD","UG","UR"), "min_hours": 3, "flags": ["az","am","ge","ru"]},
        "vACC Ukraine": {"prefixes": ("UK",), "min_hours": 3, "flags": ["ua"]},
    }

    totals = {k: 0 for k in groups}; active_groups = set()
    for s in all_sessions:
        cs = s.get("callsign", "").upper()
        for name, info in groups.items():
            if cs.startswith(info["prefixes"]): active_groups.add(name); break
    for s in filtered_data:
        cs = s.get("callsign", "").upper()
        mins = float(s.get("minutes_on_callsign", 0))
        for name, info in groups.items():
            if cs.startswith(info["prefixes"]):
                if name not in excluded: totals[name] += mins
                break

    total_all_minutes = sum(totals.values())
    home_name = None; home_minutes = 0
    if home_override and home_override in groups and home_override in active_groups and home_override not in excluded:
        home_name = home_override; home_minutes = totals[home_override]
        home_is_manual = True
    else:
        if totals:
            home_name = max(totals, key=lambda k: totals[k]); home_minutes = totals[home_name]
            if home_minutes == 0: home_name = None
        home_is_manual = False

    selectable_homes = sorted([n for n in active_groups if n not in excluded])

    visiting_minutes = total_all_minutes - home_minutes
    home_meets_50 = home_minutes >= visiting_minutes and total_all_minutes > 0
    home_buffer = max(home_minutes - visiting_minutes, 0)
    home_need = max(visiting_minutes - home_minutes, 0)
    home_flags = groups[home_name]["flags"] if home_name and home_name in groups else []
    period_stats = {"total_hhmm": format_hhmm(total_all_minutes), "home_name": home_name,
        "home_flags": home_flags, "home_hhmm": format_hhmm(home_minutes),
        "visiting_hhmm": format_hhmm(visiting_minutes), "home_meets_50": home_meets_50,
        "home_buffer_hhmm": format_hhmm(home_buffer), "home_need_hhmm": format_hhmm(home_need),
        "has_data": total_all_minutes > 0, "is_manual": home_is_manual}

    goal_calc = None
    if total_all_minutes > 0 and home_name:
        MIN_MIN = 3 * 60

        visiting_divs = {
            name: totals[name]
            for name in active_groups
            if name != home_name and name not in excluded
        }

        visiting_need = sum(max(0, MIN_MIN - m) for m in visiting_divs.values())
        home_min_need = max(0, MIN_MIN - home_minutes)
        future_visiting = sum(max(m, MIN_MIN) for m in visiting_divs.values())

        home_for_50_total = max(0, future_visiting - home_minutes)
        home_add_total = max(home_min_need, home_for_50_total)

        home_50_now = max(0, visiting_minutes - home_minutes)

        final_needed = home_add_total + visiting_need

        goal_calc = {
            "needed_min": final_needed,
            "needed_hhmm": format_hhmm(final_needed),
            "home_50_now_hhmm": format_hhmm(home_50_now),
            "home_min_need_hhmm": format_hhmm(home_min_need),
            "visiting_need_hhmm": format_hhmm(visiting_need),
            "home_for_50_total_hhmm": format_hhmm(home_for_50_total),
            "home_add_total_hhmm": format_hhmm(home_add_total),
        }

    def future_expiry(sessions, prefixes, min_hours):
        if mode != "rolling": return "Half/Quarter mode"
        rel = [s for s in sessions if s["callsign"].upper().startswith(prefixes)]
        times = [(s["start_dt"].date(), float(s["minutes_on_callsign"])) for s in rel]
        td = datetime.utcnow().date(); ed = td + timedelta(weeks=52)
        ct = sum(m for d, m in times if td - timedelta(weeks=13) <= d <= td)
        if ct / 60 < min_hours: return "Not fulfilled"
        cd = td
        while cd <= ed:
            ws = cd - timedelta(weeks=13)
            tm = sum(m for d, m in times if ws <= d <= cd)
            if tm / 60 < min_hours: return cd.strftime("%Y-%m-%d")
            cd += timedelta(days=1)
        return "Will not expire"

    results = []; fulfilled_count = 0; not_fulfilled_count = 0
    for name, info in groups.items():
        if name not in active_groups: continue
        if name in excluded: continue
        mins = totals[name]; hours = mins / 60
        pct = (mins / total_all_minutes * 100) if total_all_minutes else 0
        st = hours >= info["min_hours"]
        if st: fulfilled_count += 1
        else: not_fulfilled_count += 1
        exp = future_expiry(filtered_data, info["prefixes"], info["min_hours"])
        ld = [s["start_dt"] for s in all_sessions if s.get("callsign", "").upper().startswith(info["prefixes"])]
        if ld:
            lc = max(ld); da = (datetime.utcnow() - lc).days; lct = f"{da}d ago"
        else: da = 999999; lct = "Never"
        results.append({"name": name, "flags": info["flags"], "minutes": mins,
            "hours": round(hours,2), "hours_hhmm": format_hhmm(mins),
            "minimum": info["min_hours"], "minimum_hhmm": format_hhmm(info["min_hours"]*60),
            "percent": round(pct,1), "status": st, "expiry": exp,
            "last_controlled": lct, "last_controlled_days": da})

    def esk(x):
        e = x["expiry"]
        if e == "Will not expire": return datetime.max
        if e in ["Not fulfilled", "Half/Quarter mode"]: return datetime.min
        try: return datetime.strptime(e, "%Y-%m-%d")
        except: return datetime.max

    if sort_by == "expiry": results.sort(key=esk)
    elif sort_by == "last": results.sort(key=lambda x: x["last_controlled_days"])
    else: results.sort(key=lambda x: x["hours"], reverse=True)

    heatmap = build_heatmap(all_sessions)
    all_fulfilled = fulfilled_count > 0 and not_fulfilled_count == 0

    return render_template(
        "index.html",
        results=results,
        request=request,
        mode=mode,
        available_quarters=available_quarters,
        available_periods=available_periods,
        pilot_name=pilot_name,
        fulfilled_count=fulfilled_count,
        not_fulfilled_count=not_fulfilled_count,
        top_callsigns=top_callsigns,
        all_callsigns=all_callsigns,
        period_stats=period_stats,
        goal_calc=goal_calc,
        heatmap=heatmap,
        all_fulfilled=all_fulfilled,
        excluded=excluded,
        period_countdown=period_countdown,
        selectable_homes=selectable_homes,
        home_override=home_override
    )

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))