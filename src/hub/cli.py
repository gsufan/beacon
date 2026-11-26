"""`beacon-hub`: starts the Hub service."""

import argparse
import os


def main():
    parser = argparse.ArgumentParser(description="Beacon Hub: cuentas, roles, licencias, auditoría y GitHub.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--data", default=os.environ.get("BEACON_HUB_DATA", "hub-data"), help="carpeta de datos del Hub")
    args = parser.parse_args()
    os.environ["BEACON_HUB_DATA"] = args.data

    import uvicorn
    uvicorn.run("hub.app:default_app", factory=True, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
