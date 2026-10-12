"""Stand-in modules for the job runner tests: behaviour chosen by the first argument."""
import sys

from pipeline import store


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "ok"
    if what == "ok":
        conn = store.connect()
        conn.execute("INSERT INTO headlines VALUES ('a1', 'A title', '2026-10-12T00:00:00Z', 'u', 'original')")
        conn.commit()
        print("wrote one headline")
        print("second line with a key sk-abcdef1234567890 in it")
    elif what == "crash_in_tx":                      # dies with an uncommitted write: the record must still be saved
        conn = store.connect()
        conn.execute("INSERT INTO headlines VALUES ('a2', 'Half written', '2026-10-12T00:00:00Z', 'u', 'original')")
        raise RuntimeError("crashed mid-transaction")
    elif what == "boom":
        raise RuntimeError("provider said: Incorrect API key sk-abcdef1234567890")
    elif what == "exit3":
        raise SystemExit(3)
    elif what == "args":
        print("args " + " ".join(sys.argv[1:]))
