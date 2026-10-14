# -*- coding: utf-8 -*-
"""室外天气抓取（Open-Meteo 免费 API，无需注册/密钥）。

用于给采集数据附加室外温度/湿度——这是热模型标定的边界条件。

Open-Meteo 特点：
  · 完全免费、无需 API key
  · 返回 UTC 时间，代码里换算成本地时间
  · 默认坐标：上海市闵行区

用法：
    python fetch_weather.py            # 打印当前室外天气
    python fetch_weather.py --json     # 只输出 JSON
"""

import argparse
import json
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# 上海市闵行区
LAT = 31.11
LON = 121.38
TZ_OFFSET_HOURS = 8        # 东八区

API = ("https://api.open-meteo.com/v1/forecast"
       "?latitude={lat}&longitude={lon}"
       "&current=temperature_2m,relative_humidity_2m,apparent_temperature"
       "&hourly=temperature_2m,relative_humidity_2m"
       "&forecast_days=1")


def fetch(timeout=15):
    """抓取当前室外天气，返回 dict；失败返回 None。"""
    url = API.format(lat=LAT, lon=LON)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError, TimeoutError) as e:
        return {"error": str(e)}

    cur = data.get("current", {})
    ts_utc = cur.get("time")
    local = None
    if ts_utc:
        try:
            dt = datetime.fromisoformat(ts_utc).replace(tzinfo=timezone.utc)
            local = dt.astimezone(timezone(timedelta(hours=TZ_OFFSET_HOURS)))
        except ValueError:
            pass

    return {
        "outdoor_temp_c": cur.get("temperature_2m"),
        "outdoor_humidity_pct": cur.get("relative_humidity_2m"),
        "outdoor_feels_c": cur.get("apparent_temperature"),
        "api_time_utc": ts_utc,
        "local_time": local.strftime("%Y-%m-%d %H:%M:%S") if local else None,
        "elevation_m": data.get("elevation"),
    }


def main():
    ap = argparse.ArgumentParser(description="抓取室外天气（Open-Meteo）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    w = fetch()
    if args.json:
        print(json.dumps(w, ensure_ascii=False))
        return

    if "error" in w:
        print(f"抓取失败: {w['error']}")
        return
    print("=== 上海闵行区 室外天气 ===")
    print(f"  温度     : {w['outdoor_temp_c']} °C")
    print(f"  湿度     : {w['outdoor_humidity_pct']} %")
    print(f"  体感     : {w['outdoor_feels_c']} °C")
    print(f"  本地时间 : {w['local_time']}")
    print(f"  数据时间 : {w['api_time_utc']} (UTC)")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
