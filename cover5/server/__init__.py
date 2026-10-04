"""Local web app: FastAPI over cover5.service, the built React frontend, and the scheduler.

    python -m cover5 serve [--open] [--no-scheduler] [--host H] [--port P]
"""
from __future__ import annotations

import threading
import time
import webbrowser


def serve(host: str = "127.0.0.1", port: int = 8765, scheduler: bool = True,
          open_browser: bool = False) -> None:
    """Run the API in-process (one uvicorn worker) until Ctrl+C."""
    import uvicorn

    from cover5.server import settings as settings_mod
    from cover5.server.app import FRONTEND_DIST, create_app

    app = create_app(scheduler_allowed=scheduler)
    sched = app.state.scheduler
    shown_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{shown_host}:{port}"

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, workers=1, log_level="info"))

    def _after_start():
        while not server.started and not server.should_exit:
            time.sleep(0.1)
        if not server.started:
            return
        if scheduler and settings_mod.load()["scheduler"]["enabled"]:
            sched.start()
        if open_browser:
            webbrowser.open(url)

    print(f"Cover 5 is running at {url}  (Ctrl+C to stop)")
    if not (FRONTEND_DIST / "index.html").exists():
        print("The web app is not built yet: npm --prefix frontend install && npm --prefix frontend run build")
    if not scheduler:
        print("Background scheduler is off (--no-scheduler).")
    threading.Thread(target=_after_start, name="cover5-startup", daemon=True).start()
    try:
        server.run()
    finally:
        sched.stop()


__all__ = ["serve"]
