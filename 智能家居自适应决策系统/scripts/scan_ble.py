# -*- coding: utf-8 -*-
"""深度扫描 BLE：列出所有设备的广播信息，尽力识别 LD2410C 雷达。"""
import asyncio
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from bleak import BleakScanner


async def main():
    seconds = 25
    print(f"深度扫描 BLE {seconds} 秒…（雷达需保持通电）")
    print("=" * 78)

    found = {}

    def cb(device, adv):
        found[device.address] = {
            "name": device.name or adv.local_name or "",
            "rssi": adv.rssi,
            "uuids": [str(u) for u in (adv.service_uuids or [])],
            "mfr": {},
        }
        for cid, data in (adv.manufacturer_data or {}).items():
            found[device.address]["mfr"][cid] = data.hex()

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(seconds)
    await scanner.stop()

    print(f"共发现 {len(found)} 个 BLE 设备\n")
    hits = []
    for addr, d in sorted(found.items(), key=lambda x: -(x[1]["rssi"] or -999)):
        name = d["name"] or "(无名)"
        line = f"  {addr:<20}{d['rssi']:>5} dBm  {name}"
        if d["uuids"]:
            line += "  UUID:" + ",".join(u[:8] for u in d["uuids"])
        if d["mfr"]:
            m = []
            for cid, h in d["mfr"].items():
                cname = {76: "Apple", 6: "Microsoft", 1177: "Nordic",
                         280: "小米", 89: "Nordic?"}.get(cid, str(cid))
                m.append(f"{cname}:{h[:20]}")
            line += "  MFG[" + " ".join(m) + "]"
        print(line)

        low = (d["name"] or "").upper()
        if any(k in low for k in ("LD2410", "HLK", "RADAR", "2410")):
            hits.append((addr, d))

    print()
    print("=" * 78)
    if hits:
        for addr, d in hits:
            print(f"*** 找到雷达：{d['name']}  地址 {addr}  信号 {d['rssi']} dBm")
        print("\n-> 雷达蓝牙广播正常，手机 App 应该能连上")
    else:
        print("仍未发现名称含 LD2410 / HLK / Radar 的设备。")
        print()
        print("提示：LD2410 的 BLE 广播间隔较长（省电设计），")
        print("      且 Windows 蓝牙栈对部分设备不显示名称。")
        print("      手机 App 通常比 Windows 更容易发现它。")
        print()
        print("请直接用手机装 HLKRadarTool 试连，以手机结果为准。")


asyncio.run(main())
