"""Read-only diagnostics by default; movement only in explicitly enabled serve mode."""
import argparse
import json
import logging
import signal
import sys

from .config import load
from .model import safe_data
from .protocol import Connector, ProtocolError, UDPTransport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("action", choices=("discover", "inspect", "serve"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        config = load(args.config)
        if args.action == "serve":
            from .service import Service
            service = Service(config)
            for signum in (signal.SIGTERM, signal.SIGINT):
                signal.signal(signum, lambda *_: service.stop.set())
            service.run()
        else:
            bridge = Connector(UDPTransport(config.bridge_host), config.key)
            devices = bridge.discover()
            result = {"devices": devices}
            if args.action == "inspect":
                result["snapshots"] = {s.id: {"source": "bridge_cache", "data": safe_data(bridge.device_request(s.mac))}
                                       for s in config.shades}
            print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, ProtocolError) as exc:
        # No raw exception message: malformed files/remote messages might contain credentials.
        print(f"Operation failed ({type(exc).__name__}); check configuration, credentials and bridge reachability.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
