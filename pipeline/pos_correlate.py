"""
pos_correlate.py — Correlate POS transactions with visitor sessions

Logic (from problem statement):
  A visitor who was in the BILLING zone in the 5-minute window BEFORE 
  a transaction timestamp counts as a converted visitor for that session.

Additional logic:
  - BILLING_QUEUE_JOIN: emitted when >1 person in billing zone simultaneously
  - BILLING_QUEUE_ABANDON: visitor left billing zone without a transaction
    following within 5 minutes
"""

import json
import csv
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict


TRANSACTION_WINDOW_MINUTES = 5
STORE_ID = "ST1008"
CAMERA_ID = "CAM_5"


def load_pos(csv_path: str) -> list:
    """Load POS transactions, deduplicated by order_id with summed total."""
    orders = {}
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            oid = row["order_id"]
            ts_str = f"{row['order_date']} {row['order_time']}"
            try:
                ts = datetime.strptime(ts_str, "%d-%m-%Y %H:%M:%S")
            except ValueError:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            amt = float(row.get("total_amount", 0) or 0)
            if oid not in orders:
                orders[oid] = {"order_id": oid, "timestamp": ts, "amount": 0.0}
            orders[oid]["amount"] += amt
    return sorted(orders.values(), key=lambda x: x["timestamp"])


def load_billing_events(events_path: str) -> list:
    """Load billing-zone events from JSONL."""
    billing = []
    with open(events_path) as f:
        for line in f:
            ev = json.loads(line.strip())
            if ev.get("zone_id") == "BILLING" and not ev.get("is_staff"):
                ev["_ts"] = datetime.strptime(ev["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
                billing.append(ev)
    return sorted(billing, key=lambda x: x["_ts"])


def correlate(events_path: str, pos_path: str, output_path: str):
    """
    Main correlation logic.
    Emits BILLING_QUEUE_JOIN and BILLING_QUEUE_ABANDON events.
    Returns conversion metrics dict.
    """
    transactions = load_pos(pos_path)
    billing_events = load_billing_events(events_path)

    converted_visitors = set()
    abandoned_visitors = set()
    new_events = []

    # Track who's in billing at any point
    in_billing = {}  # visitor_id → enter_ts
    for ev in billing_events:
        vid = ev["visitor_id"]
        ets = ev["_ts"]
        etype = ev["event_type"]

        if etype == "ZONE_ENTER":
            in_billing[vid] = ets
            # Check queue depth (how many others currently in billing)
            others_in = sum(1 for v, t in in_billing.items()
                           if v != vid and (ets - t).total_seconds() < 300)
            if others_in > 0:
                new_events.append({
                    "event_id": str(uuid.uuid4()),
                    "store_id": STORE_ID,
                    "camera_id": CAMERA_ID,
                    "visitor_id": vid,
                    "event_type": "BILLING_QUEUE_JOIN",
                    "timestamp": ev["timestamp"],
                    "zone_id": "BILLING",
                    "dwell_ms": 0,
                    "is_staff": False,
                    "confidence": ev["confidence"],
                    "metadata": {
                        "queue_depth": others_in,
                        "sku_zone": None,
                        "session_seq": ev["metadata"]["session_seq"],
                    },
                })

        elif etype in ("ZONE_EXIT", "EXIT"):
            enter_ts = in_billing.pop(vid, ets - timedelta(seconds=60))
            # Check if a transaction followed within 5 min
            converted = any(
                0 <= (tx["timestamp"] - ets).total_seconds() <= TRANSACTION_WINDOW_MINUTES * 60
                for tx in transactions
            )
            if converted:
                converted_visitors.add(vid)
            else:
                dwell = (ets - enter_ts).total_seconds()
                if dwell > 30:  # Was actually in billing for >30s — real abandon
                    abandoned_visitors.add(vid)
                    new_events.append({
                        "event_id": str(uuid.uuid4()),
                        "store_id": STORE_ID,
                        "camera_id": CAMERA_ID,
                        "visitor_id": vid,
                        "event_type": "BILLING_QUEUE_ABANDON",
                        "timestamp": ev["timestamp"],
                        "zone_id": "BILLING",
                        "dwell_ms": int(dwell * 1000),
                        "is_staff": False,
                        "confidence": ev["confidence"],
                        "metadata": {
                            "queue_depth": 0,
                            "sku_zone": None,
                            "session_seq": ev["metadata"]["session_seq"],
                        },
                    })

    # Append new events to main events file
    with open(events_path, "a") as f:
        for ev in new_events:
            f.write(json.dumps(ev) + "\n")

    print(f"[POS] Converted visitors: {len(converted_visitors)}")
    print(f"[POS] Abandoned visitors: {len(abandoned_visitors)}")
    print(f"[POS] New events emitted: {len(new_events)}")
    return {
        "converted": list(converted_visitors),
        "abandoned": list(abandoned_visitors),
        "total_transactions": len(transactions),
    }


if __name__ == "__main__":
    import sys
    events_path = sys.argv[1] if len(sys.argv) > 1 else "output/all_events.jsonl"
    pos_path = sys.argv[2] if len(sys.argv) > 2 else \
        "/mnt/user-data/uploads/Brigade_Bangalore_10_April_26__1_bc6219c.csv"
    correlate(events_path, pos_path, "output/correlation_result.json")
