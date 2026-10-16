"""Behavioral check for the btapistore ExchangeID fallback (SCN02 014_2 livefix).

Runs the REAL BtApiStore CTP wrapper class in-process with a fake md client and
fake tick payloads. No network, no files touched outside stdout.
"""
import sys
import types

sys.path.insert(0, r"D:\source_code\backtrader")

from backtrader.stores import btapistore as S

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    if not cond:
        failures.append(name)
    print(f"{status} {name} {detail}")


def make_payload(instrument, exchange_id, price=1000.0, volume=10.0):
    p = types.SimpleNamespace()
    p.InstrumentID = instrument
    p.ExchangeID = exchange_id
    p.LastPrice = price
    p.Volume = volume
    p.UpdateTime = "10:00:00"
    p.UpdateMillisec = 0
    p.ActionDay = "20261016"
    p.TradingDay = "20261016"
    p.BidPrice1 = price - 1
    p.AskPrice1 = price + 1
    return p


def last_tick(wrapper, instrument):
    queue = wrapper._tick_queues.get(instrument)
    if not queue:
        return None
    return queue[-1]


print("== 1. pure helper _infer_ctp_instrument_exchange ==")
cases = [
    ("SA701", "CZCE"),
    ("SA701C1080", "CZCE"),
    ("SA701P1080", "CZCE"),
    ("SR801", "CZCE"),
    ("UR2505", "CZCE"),
    ("CF601", "CZCE"),
    ("UR2505C1900", ""),
    ("rb2510", ""),
    ("rb2510C3300", ""),
    ("m2501C2900", ""),
    ("IO2501C4000", ""),
    ("IF2501", ""),
    ("", ""),
    (None, ""),
    ("   ", ""),
]
for value, expected in cases:
    got = S._infer_ctp_instrument_exchange(value)
    check(f"infer({value!r})", got == expected, f"got={got!r} expected={expected!r}")


class FakeMdClient:
    def __init__(self):
        self.calls = []

    def subscribe(self, symbols):
        self.calls.append(list(symbols))


Wrapper = S._create_ctp_wrapper_class()


def new_wrapper():
    w = Wrapper()
    w.md_client = FakeMdClient()
    return w


print("== 2. subscribe retains exchange / infers CZCE for bare aliases ==")
w = new_wrapper()
w.subscribe(["SA701", "SA701C1080", "SA701P1080"])
check("md got deduped instruments",
      w.md_client.calls == [["SA701", "SA701C1080", "SA701P1080"]],
      f"calls={w.md_client.calls}")
check("map all CZCE",
      w._instrument_exchanges == {"SA701": "CZCE", "SA701C1080": "CZCE", "SA701P1080": "CZCE"},
      f"map={w._instrument_exchanges}")

print("== 3. tick with EMPTY venue ExchangeID gets fallback exchange ==")
w._handle_md_tick(make_payload("SA701", "", price=1000.0, volume=5.0))
tick = last_tick(w, "SA701")
check("SA701 tick exists", tick is not None)
check("SA701 tick exchange == CZCE", tick is not None and tick.exchange == "CZCE",
      f"exchange={getattr(tick, 'exchange', None)!r}")
check("SA701 tick exchange_id == CZCE",
      tick is not None and getattr(tick, "exchange_id", None) == "CZCE",
      f"exchange_id={getattr(tick, 'exchange_id', None)!r}")

w._handle_md_tick(make_payload("SA701C1080", "", price=120.0, volume=3.0))
tick = last_tick(w, "SA701C1080")
check("SA701C1080 option tick exchange == CZCE",
      tick is not None and tick.exchange == "CZCE",
      f"exchange={getattr(tick, 'exchange', None)!r}")

print("== 4. venue-provided ExchangeID path is untouched ==")
w2 = new_wrapper()
w2.subscribe(["SA701"])
w2._handle_md_tick(make_payload("SA701", "CZCE", price=1000.0, volume=5.0))
tick = last_tick(w2, "SA701")
check("provided CZCE stays CZCE", tick is not None and tick.exchange == "CZCE",
      f"exchange={getattr(tick, 'exchange', None)!r}")
w2._handle_md_tick(make_payload("SA701", "czce", price=1001.0, volume=7.0))
tick = last_tick(w2, "SA701")
check("provided lowercase czce -> CZCE (as before)",
      tick is not None and tick.exchange == "CZCE",
      f"exchange={getattr(tick, 'exchange', None)!r}")

print("== 5. unsubscribed / non-inferable instrument behaves as today (empty) ==")
w3 = new_wrapper()
w3.subscribe(["rb2510"])
check("rb2510 not in map", "rb2510" not in w3._instrument_exchanges,
      f"map={w3._instrument_exchanges}")
w3._handle_md_tick(make_payload("rb2510", "", price=3000.0, volume=1.0))
tick = last_tick(w3, "rb2510")
check("rb2510 tick exchange stays ''", tick is not None and tick.exchange == "",
      f"exchange={getattr(tick, 'exchange', None)!r}")
w3._handle_md_tick(make_payload("XX9999", "", price=1.0, volume=1.0))
tick = last_tick(w3, "XX9999")
check("unsubscribed XX9999 tick exchange stays ''", tick is not None and tick.exchange == "",
      f"exchange={getattr(tick, 'exchange', None)!r}")

print("== 6. typed alias precedence and retention ==")
w4 = new_wrapper()
w4.subscribe(["CZCE.SR801", "SHFE.rb2510"])
check("typed exchange retained",
      w4._instrument_exchanges == {"SR801": "CZCE", "rb2510": "SHFE"},
      f"map={w4._instrument_exchanges}")
check("aliases recorded",
      w4._instrument_aliases["SR801"] == {"CZCE.SR801"},
      f"aliases={dict(w4._instrument_aliases)}")
w4.subscribe(["SA701"])
check("bare SA701 inferred after typed subs", w4._instrument_exchanges.get("SA701") == "CZCE",
      f"map={w4._instrument_exchanges}")
# explicit typed exchange overrides inference (explicit wins, last typed wins)
w5 = new_wrapper()
w5.subscribe(["SA701"])
check("SA701 inferred CZCE", w5._instrument_exchanges.get("SA701") == "CZCE",
      f"map={w5._instrument_exchanges}")
w5.subscribe(["SHFE.SA701"])
check("explicit typed exchange overrides inference",
      w5._instrument_exchanges.get("SA701") == "SHFE",
      f"map={w5._instrument_exchanges}")
# inference must NOT overwrite an existing explicit entry
w6 = new_wrapper()
w6.subscribe(["SHFE.rb2510"])
w6.subscribe(["rb2510"])
check("bare alias does not clobber typed exchange",
      w6._instrument_exchanges.get("rb2510") == "SHFE",
      f"map={w6._instrument_exchanges}")

print("== 7. empty symbol subscribe is a no-op ==")
w7 = new_wrapper()
w7.subscribe(["", None, "  "])
check("no md subscribe call for empties", w7.md_client.calls == [],
      f"calls={w7.md_client.calls}")

print()
if failures:
    print(f"RESULT: FAIL ({len(failures)} failing checks)")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("RESULT: ALL CHECKS PASS")
