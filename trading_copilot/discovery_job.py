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
    is an error, never an empty result,
  * the optional AI analysis is a second phase: the picks are published
    before it starts, and if it fails the scan still stands -- the job
    finishes "done" with `analysis_error` set, not "error".
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
            "phase": None,              # scanning | analyzing, while running
            "started_at": None,
            "finished_at": None,
            "scanned": 0,
            "total": None,
            "picks": None,              # the screener's top picks, as measured
            "universe": None,           # every stock scored (symbol, score, rs, adv, bias)
            "watchlist_written": None,  # did the scan rewrite watchlist.csv?
            "live_added": None,         # tokens now streaming in this session
            "subscription_pending": None,  # added, but socket down: subscribes on reconnect
            "removals_deferred": None,  # dropped from watchlist.csv; live until restart
            "apply_error": None,        # file written, live update failed
            "error": None,
            "analysis": None,           # discovery_analysis.analyze_top output
            "analysis_error": None,
        }

    def status(self) -> dict:
        return dict(self._status)

    def start(self, scan, apply, analyze=None) -> bool:
        """Launch a run. `scan(progress)` -> (picks, selected-or-None[, universe]);
        `apply(selected)` -> (live_added, removals_deferred[, pending]);
        `analyze(picks)` -> analysis dict or None (optional)."""
        if self._task is not None and not self._task.done():
            return False
        self._status = self._blank("running")
        self._status["started_at"] = _now_iso()
        self._status["phase"] = "scanning"
        self._task = asyncio.get_running_loop().create_task(self._run(scan, apply, analyze))
        return True

    def _progress(self, scanned: int, total: int) -> None:
        self._status["scanned"], self._status["total"] = scanned, total

    async def _run(self, scan, apply, analyze) -> None:
        try:
            picks, selected, *more = await scan(self._progress)
            self._status["picks"] = list(picks or [])
            self._status["universe"] = list(more[0]) if more and more[0] is not None else None
            # run_scan writes watchlist.csv exactly when it returns a selection.
            self._status["watchlist_written"] = bool(selected)
            if selected:
                # The file is already written, so a failure from here on is
                # a failed LIVE update -- the scan and the file both stand.
                try:
                    added, deferred, *rest = await apply(selected)
                    self._status.update(live_added=list(added), removals_deferred=list(deferred),
                                        subscription_pending=list(rest[0]) if rest else [])
                except Exception as e:
                    logger.error(f"Discovery live apply failed; watchlist.csv already updated: {e}")
                    self._status["apply_error"] = f"{type(e).__name__}: {e}"
            else:
                # Selection failed or found nothing: run_scan left
                # watchlist.csv alone, so the live feed is left alone too.
                self._status.update(live_added=[], removals_deferred=[], subscription_pending=[])
            if analyze is not None and picks:
                self._status["phase"] = "analyzing"
                try:
                    self._status["analysis"] = await analyze(self._status["picks"])
                except Exception as e:
                    logger.error(f"Discovery analysis failed; scan results kept: {e}")
                    self._status["analysis_error"] = f"{type(e).__name__}: {e}"
            self._status["state"] = "done"
        except Exception as e:
            logger.error(f"Discovery run failed: {e}")
            self._status.update(state="error", error=f"{type(e).__name__}: {e}")
        finally:
            self._status["phase"] = None
            self._status["finished_at"] = _now_iso()
