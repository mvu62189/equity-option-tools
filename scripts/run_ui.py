from __future__ import annotations

import logging

from flow_app.legacy import run_ui_legacy


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    raise SystemExit(run_ui_legacy())
