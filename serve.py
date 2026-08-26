"""Start the dashboard.

    python serve.py            -> http://localhost:8787
    python serve.py 9000       -> different port
    python serve.py 8787 all   -> bind 0.0.0.0 (VPS; put it behind a firewall
                                  or an SSH tunnel, it has no auth)
"""
import sys

from aa.web import serve

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    host = "0.0.0.0" if len(sys.argv) > 2 and sys.argv[2] == "all" else "127.0.0.1"
    serve(port=port, host=host)
