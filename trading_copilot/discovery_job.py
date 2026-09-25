"""Discovery as a background job owned by the feed process.

Replaces the request-scoped playbook pipeline (A-7). That one ran a
full-universe scan inside a single HTTP request in the API process, with a
second Upstox login, enriched candidates from a live-state map that only
covers the current watchlist (so OBI/CVD were "N/A" for nearly all of
them), and then asked the LLM to invent entry/target/stoploss prices and a
confidence grade. None of that survives here.

The job is deliberately ignorant of Upstox and FastAPI: `scan` and `apply`
are injected, so its guarantees are testable on their own --
  * start() returns immediately; the scan runs as a task,
  * a start while running is refused rather than launching a second scan,
  * status() reports only what the scan and the apply step actually did:
    `picks` is None (not []) until a scan has finished, and a failed scan
    is an error, never an empty result.
"""
import asyncio
import datetime
import logging

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


class DiscoveryJob:
    def __init__(self):
        self._task = None
        self._status = self._blank("idle")

    @staticmethod
    def _blank(state: str) -> dict:
        return {
            "state": state,             # idle | running | done | error
            "started_at": None,
            "finished_at": None,
            "scanned": 0,
            "total": None,
            "picks": None,              # the screener's top picks, as measured
            "live_added": None,         # tokens now streaming in this session
            "removals_deferred": None,  # dropped from watchlist.csv; live until restart
            "error": None,
        }

    def status(self) -> dict:
        return dict(self._status)

    def start(self, scan, apply) -> bool:
        """Launch a run. `scan(progress)` -> (picks, selected-or-None);
        `apply(selected)` -> (live_added, removals_deferred)."""
        if self._task is not None and not self._task.done():
            return False
        self._status = self._blank("running")
        self._status["started_at"] = _now_iso()
        self._task = asyncio.get_running_loop().create_task(self._run(scan, apply))
        return True

    def _progress(self, scanned: int, total: int) -> None:
        self._status["scanned"], self._status["total"] = scanned, total

    async def _run(self, scan, apply) -> None:
        try:
            picks, selected = await scan(self._progress)
            if selected:
                added, deferred = await apply(selected)
            else:
                # Selection failed or found nothing: run_scan left
                # watchlist.csv alone, so the live feed is left alone too.
                added, deferred = [], []
            self._status.update(state="done", picks=list(picks or []),
                                live_added=list(added), removals_deferred=list(deferred))
        except Exception as e:
            logger.error(f"Discovery run failed: {e}")
            self._status.update(state="error", error=f"{type(e).__name__}: {e}")
        finally:
            self._status["finished_at"] = _now_iso()
