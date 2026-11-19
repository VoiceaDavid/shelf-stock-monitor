"""Shelf simulator: behaves like the ESP32 shelf node, so the cloud side and the
Shopify sync can be tested and demonstrated without hardware.

Usage (from the repository root, after scripts/setup_broker.sh):
    pip install paho-mqtt
    python tools/simulator.py --host localhost --device shelf-sim --password <pw>

Commands while running:
    <pos> <count>   set position <pos> to <count> items, e.g. "1 7"
    hb              send a heartbeat now
    q               quit
Each change is published as an event with an increasing sequence number,
exactly like the firmware does (after its 6 s stability wait).
"""
import argparse
import json
import ssl
import threading
import time

import paho.mqtt.client as mqtt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=8883)
    ap.add_argument("--device", default="shelf-sim")
    ap.add_argument("--password", required=True)
    ap.add_argument("--ca", default="broker/certs/ca.crt")
    ap.add_argument("--positions", type=int, default=3)
    ap.add_argument("--start", type=int, default=10, help="initial items per position")
    ap.add_argument("--script", help="semicolon-separated commands to run, then exit, e.g. '1 7;1 6'")
    args = ap.parse_args()

    counts = {p: args.start for p in range(1, args.positions + 1)}
    seq = [int(time.time())]  # increases across restarts, like the persisted seq on the ESP32
    base = f"shelf/{args.device}"

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=args.device)
    c.username_pw_set(args.device, args.password)
    c.tls_set(ca_certs=args.ca, tls_version=ssl.PROTOCOL_TLS_CLIENT)
    connected = threading.Event()

    def heartbeat() -> None:
        c.publish(f"{base}/heartbeat", json.dumps({
            "uptime_s": 0, "rssi": -55,
            "positions": [{"pos": p, "count": n, "ok": True, "calibrated": True} for p, n in counts.items()],
        }))

    def event(pos: int, new: int) -> None:
        old = counts[pos]
        counts[pos] = new
        seq[0] += 1
        c.publish(f"{base}/event", json.dumps({"pos": pos, "old": old, "new": new, "seq": seq[0], "ok": True}), qos=1)
        print(f"  event P{pos}: {old} -> {new} (seq {seq[0]})")

    def on_connect(client, userdata, flags, rc, props):
        if rc.is_failure:
            print("connect failed:", rc)
            return
        print(f"connected as {args.device}")
        client.subscribe(f"{base}/cmd", qos=1)
        heartbeat()
        connected.set()

    def on_message(client, userdata, msg):
        cmd = json.loads(msg.payload)
        pos, action = int(cmd["pos"]), cmd["action"]
        print(f"  command from owner page: {cmd}")
        target = 0 if action == "tare" else int(cmd.get("items", 10))
        client.publish(f"{base}/calib", json.dumps({
            "pos": pos, "action": action, "ok": True, "msg": "simulated", "raw_per_item": 1000.0, "count": target}))
        if counts.get(pos) != target:
            event(pos, target)

    c.on_connect = on_connect
    c.on_message = on_message
    c.connect(args.host, args.port, keepalive=30)
    c.loop_start()
    if not connected.wait(10):
        raise SystemExit("could not connect to the broker")

    def beat_forever():
        while True:
            time.sleep(30)
            heartbeat()

    threading.Thread(target=beat_forever, daemon=True).start()

    def handle(line: str) -> bool:
        """Runs one command; returns False to stop."""
        line = line.strip()
        if line == "q":
            return False
        if line == "hb":
            heartbeat()
            return True
        try:
            pos, new = (int(x) for x in line.split())
            if pos not in counts or not 0 <= new <= 20:
                raise ValueError
        except ValueError:
            print("type '<position> <count>', e.g. '1 7' (count 0-20), 'hb' or 'q'")
            return True
        event(pos, new)
        return True

    if args.script:
        for line in args.script.split(";"):
            handle(line)
            time.sleep(0.5)
        time.sleep(1)
    else:
        while handle(input("> ")):
            pass
    c.loop_stop()


if __name__ == "__main__":
    main()
