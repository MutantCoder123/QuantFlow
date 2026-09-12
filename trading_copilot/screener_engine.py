import os
import csv
import asyncio
import logging
from functools import lru_cache

import yaml

from config import load_watchlist_from_csv
from historical_engine import HistoricalFetcher
from derivatives_engine import OptionsAnalyzer
from technical_engine import MathEngine
from scrip_master_engine import get_all_fno_equities
from core.risk import cluster_of, load_clusters

logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def load_watchlist_policy(path=None):
    """The screener-managed watchlist policy (improved §4.9): core pins,
    dynamic slot count, and the liquidity/cluster/churn bounds on
    ``select_bounded_watchlist``."""
    if path is None:
        from paths import CONFIG_DIR
        path = CONFIG_DIR / "watchlist_policy.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _score_volume_shock(stock_df) -> float:
    """Volume-shock term of the magnitude score (fixes §A-15).

    Rewards volume strictly in EXCESS of the 20-period average -- a stock
    trading at or below its own average volume contributes zero. The old
    formula, ``min((live/ma)*35, 35)``, saturated at ratio 1.0, so roughly
    half the universe on any given day scored full marks for doing nothing
    unusual.
    """
    if len(stock_df) < 26:
        return 0.0
    live_volume = stock_df['volume'].iloc[-1]
    vol_20ma = stock_df['volume'].rolling(20).mean().iloc[-1]
    if not vol_20ma or vol_20ma <= 0:
        return 0.0
    ratio = live_volume / vol_20ma
    return min(max(0, ratio - 1) * 17.5, 35)


def _zscale(value: float, unit: float, cap: float = 3.0) -> float:
    """Express `value` as a bounded number of multiples of `unit` -- its own
    already-chosen significance threshold. Used to put net_polarity's three
    contributions (trend, MACD, relative-strength) on a common, comparable
    scale instead of the old arbitrary per-term multipliers that let
    whichever term had the biggest constant dominate directional_bias.
    """
    return max(min(value / unit, cap), -cap)


def select_bounded_watchlist(candidates: list, previous_symbols: list,
                              policy: dict, clusters: dict,
                              token_resolver=None) -> list:
    """Bounded, policy-driven watchlist selection (improved §4.9).

    `candidates` are scored screener results (each carrying at least
    token/symbol/exchange/score; dynamic-slot eligibility also needs
    adv_crore). `previous_symbols` is the watchlist's current dynamic-slot
    contents (core excluded). `policy` is the parsed watchlist_policy.yaml.
    `clusters` is the {symbol: cluster_id} map from core.risk.load_clusters().

    Returns core selections + final dynamic selections, each a dict with at
    least token/symbol/exchange (the shape `_update_watchlist` expects).
    """
    if token_resolver is None:
        from scrip_master_engine import get_instrument_key as token_resolver

    core_symbols = [str(s).upper() for s in (policy.get("core") or [])]
    dynamic_slots = int(policy.get("dynamic_slots", 0))
    cluster_cap = policy.get("cluster_cap")
    cluster_cap = int(cluster_cap) if cluster_cap is not None else None
    min_adv_crore = float(policy.get("min_adv_crore", 0) or 0)
    churn_cap = int(policy.get("churn_cap", 0) or 0)

    by_symbol = {str(c["symbol"]).upper(): c for c in candidates}

    # 1. Core -- always included, never counted against churn_cap.
    core_selections = []
    for sym in core_symbols:
        c = by_symbol.get(sym)
        if c is not None:
            core_selections.append({"token": c["token"], "symbol": sym,
                                     "exchange": c.get("exchange", "NSE")})
        else:
            core_selections.append({"token": token_resolver(sym), "symbol": sym,
                                     "exchange": "NSE"})

    # 2. Liquidity floor, excluding core (core is never subject to it).
    eligible = [c for c in candidates
                if str(c["symbol"]).upper() not in core_symbols
                and float(c.get("adv_crore") or 0) >= min_adv_crore]
    eligible.sort(key=lambda c: c["score"], reverse=True)

    # 3. Greedy fill of dynamic_slots, skipping candidates whose cluster is
    # already at cluster_cap among the slots filled so far this pass.
    dynamic_selections = []
    cluster_counts: dict = {}
    for c in eligible:
        if len(dynamic_selections) >= dynamic_slots:
            break
        sym = str(c["symbol"]).upper()
        cid = cluster_of(sym, clusters)
        if cluster_cap is not None and cluster_counts.get(cid, 0) >= cluster_cap:
            continue
        dynamic_selections.append(c)
        cluster_counts[cid] = cluster_counts.get(cid, 0) + 1

    # 4. Enforce churn_cap against the previous watchlist's dynamic slots.
    prev_syms = [str(s).upper() for s in previous_symbols]
    prev_set = set(prev_syms)
    new_syms_ordered = [str(c["symbol"]).upper() for c in dynamic_selections]
    new_set = set(new_syms_ordered)

    retained = [s for s in new_syms_ordered if s in prev_set]
    added = [s for s in new_syms_ordered if s not in prev_set]       # score-desc order
    dropped = [s for s in prev_syms if s not in new_set]
    churn = len(added) + len(dropped)

    if churn <= churn_cap:
        final_dynamic = dynamic_selections
    else:
        # Rank every prospective change -- an add's value is its own score
        # (bigger = more improving); a drop's value is the negative of the
        # previous-watchlist symbol's own current-run score, if known (bigger
        # = safer/less costly to drop). Keep only the churn_cap
        # highest-valued changes; revert everything else to its previous
        # state. (Exact tie-break mechanics are this function's own call --
        # only the churn_cap hard ceiling is contractual.)
        changes = [("add", s, by_symbol[s]["score"]) for s in added]
        changes += [("drop", s, -(by_symbol[s]["score"] if s in by_symbol else 0.0))
                    for s in dropped]
        changes.sort(key=lambda t: t[2], reverse=True)
        kept = changes[:churn_cap]
        kept_adds = {s for typ, s, _ in kept if typ == "add"}
        kept_drops = {s for typ, s, _ in kept if typ == "drop"}

        final_symbols = list(retained)
        final_symbols += [s for s in added if s in kept_adds]
        final_symbols += [s for s in dropped if s not in kept_drops]   # reverted

        final_dynamic = []
        for sym in final_symbols:
            if sym in by_symbol:
                final_dynamic.append(by_symbol[sym])
            else:
                # Reverted from the previous watchlist but absent from this
                # run's candidate pool (e.g. fetch failed) -- keep a minimal
                # placeholder instead of silently losing it.
                final_dynamic.append({"token": token_resolver(sym), "symbol": sym,
                                       "exchange": "NSE"})

        if len(final_dynamic) > dynamic_slots:
            final_dynamic.sort(key=lambda c: c.get("score", 0), reverse=True)
            final_dynamic = final_dynamic[:dynamic_slots]

    return core_selections + final_dynamic


class PreMarketScreener:
    def __init__(self, smart_connect):
        self.smart_connect = smart_connect
        self.watchlist_file = os.path.join(os.path.dirname(__file__), "watchlist.csv")
        self.fetcher = HistoricalFetcher()

    async def _process_single_stock(self, token, metadata, nifty_df, catalyst_cache):
        try:
            symbol = metadata.get("symbol", token)
            exchange = metadata.get("exchange", "NSE")
            
            # 1. Fetch Daily Chart
            _, stock_df = await self.fetcher._fetch_single(self.smart_connect, token, metadata, "ONE_DAY", 100)
            if stock_df is None or stock_df.empty:
                logger.debug(f"Failed on {symbol}: Missing historical dataframe.")
                return None
                
            # 2. Calculate Math Scoring Engine (V2 - Directionally Aware)
            magnitude_score = 0
            net_polarity = 0.0  # Positive = Bullish, Negative = Bearish
            news_summary = "No fresh news."
            
            if len(stock_df) >= 26:
                magnitude_score += _score_volume_shock(stock_df)

                # Trend Strength (35%)
                price = stock_df['close'].iloc[-1]
                ema21 = stock_df['close'].ewm(span=21, adjust=False).mean().iloc[-1]
                trend_dist = (price - ema21) / ema21
                if abs(trend_dist) > 0.005:  # 0.5% away from EMA21
                    magnitude_score += 15
                    net_polarity += _zscale(trend_dist, 0.005)

                ema12 = stock_df['close'].ewm(span=12, adjust=False).mean().iloc[-1]
                ema26 = stock_df['close'].ewm(span=26, adjust=False).mean().iloc[-1]
                macd_dist = (ema12 - ema26) / price
                if abs(macd_dist) > 0.002:  # Strong MACD divergence
                    magnitude_score += 20
                    net_polarity += _zscale(macd_dist, 0.002)
                    
            # 3. News Sentiment Scoring (30%)
            if symbol in catalyst_cache:
                news_data = catalyst_cache[symbol]
                
                if isinstance(news_data, dict):
                    news_summary = news_data.get('summary', 'Fresh news available.')
                    sentiment = news_data.get('sentiment', 'NEUTRAL')
                    impact = news_data.get('impact', 'LOW')
                    
                    impact_multiplier = {'HIGH': 30, 'MEDIUM': 15, 'LOW': 5}.get(impact, 5)
                    magnitude_score += impact_multiplier
                    
                    if sentiment == 'POSITIVE':
                        net_polarity += impact_multiplier
                    elif sentiment == 'NEGATIVE':
                        net_polarity -= impact_multiplier
                else:
                    # Fallback for plain string cache
                    news_summary = str(news_data)
                    magnitude_score += 15
                
            # 4. Relative Strength Outlier Multiplier (25%)
            comp_rs = MathEngine.calc_relative_strength(stock_df, nifty_df)
            if abs(comp_rs) >= 5.0:
                magnitude_score += 25
                net_polarity += _zscale(comp_rs, 5.0)

            directional_bias = "LONG" if net_polarity > 0 else "SHORT"

            # 5. Extract Structural Levels
            # NOTE (fixes §A-15): these are iloc[-1] -- the LATEST bar in the
            # fetched daily series, not necessarily the previous trading day
            # (that depends on when this runs and on Upstox's historical-vs-
            # forming-candle boundary, which isn't guaranteed here). Named
            # honestly rather than assumed.
            latest_bar_high = round(float(stock_df['high'].iloc[-1]), 2) if not stock_df.empty else 0.0
            latest_bar_low = round(float(stock_df['low'].iloc[-1]), 2) if not stock_df.empty else 0.0
            camarilla = MathEngine.calc_camarilla_pivots(stock_df)

            # Tradability floor input for select_bounded_watchlist's
            # min_adv_crore (improved §4.9): 20-session average daily traded
            # value, in rupees crore (1 crore = 1e7 rupees).
            adv_crore = round(float((stock_df['close'] * stock_df['volume']).tail(20).mean() / 1e7), 2)

            return {
                "token": token,
                "symbol": symbol,
                "exchange": exchange,
                "rs": comp_rs,
                "score": magnitude_score,
                "directional_bias": directional_bias,
                "news": news_summary,
                "latest_bar_high": latest_bar_high,
                "latest_bar_low": latest_bar_low,
                "camarilla": camarilla,
                "adv_crore": adv_crore
            }
        except Exception as e:
            logger.error(f"Failed on {metadata.get('symbol', token)}: [{type(e).__name__}] {e}")
            return None

    async def run_scan(self):
        logger.info("Initiating Phase 0 Pre-Market Screener...")
        
        # Fetch News Catalyst Cache
        catalyst_cache = {}
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:8003/state", timeout=3) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        catalyst_cache = data.get("catalyst_cache", {})
        except Exception as e:
            logger.warning(f"Could not fetch news catalyst cache: {e}")
        
        # Fetch Nifty 50 Baseline
        if HistoricalFetcher.nifty_baseline_df is None:
            index_meta = {"exchange": "NSE", "symbol": "Nifty 50"}
            _, index_df = await self.fetcher._fetch_single(self.smart_connect, "99926000", index_meta, "ONE_DAY", 100)
            HistoricalFetcher.nifty_baseline_df = index_df
            
        master_list = await get_all_fno_equities()
        if not master_list:
            logger.error("Failed to load F&O master list dynamically.")
            return []
            
        tokens = list(master_list.items())
        all_results = []
        
        # Module 3: Paced Concurrency
        chunk_size = 2
        for i in range(0, len(tokens), chunk_size):
            batch = tokens[i:i+chunk_size]
            tasks = [self._process_single_stock(token, meta, HistoricalFetcher.nifty_baseline_df, catalyst_cache) for token, meta in batch]
            
            # Gather with return_exceptions to isolate task crashes
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.error(f"Batch task exception: {r}")
                elif r is not None:
                    all_results.append(r)
                    
            # CRITICAL: Physical delay to respect API limits (max 3/sec)
            await asyncio.sleep(1.0)
            
        # 4. Filter and Sort (Phase 14.8 Math Engine)
        filtered = [r for r in all_results if r is not None]
                
        # Sort by mathematical score descending
        filtered.sort(key=lambda x: x["score"], reverse=True)
        top_picks = filtered[:20]

        # Module 4: Terminal Feedback
        logger.info(f"Successfully scanned {len(all_results)}/{len(tokens)} stocks. Top picks compiled.")

        # Close the discovery loop (improved §4.9): feed a bounded,
        # policy-driven selection back into the monitored watchlist instead
        # of leaving `_update_watchlist` uncalled. Uses the full scored
        # `filtered` pool (not just the top-20 `top_picks` slice) so a
        # previous-watchlist symbol that scored just outside the top 20 can
        # still be found and scored for churn-cap comparison.
        try:
            policy = load_watchlist_policy()
            clusters = load_clusters()
            previous_watchlist = load_watchlist_from_csv(self.watchlist_file)
            previous_symbols = [v.get("symbol", "") for v in previous_watchlist.values()]
            bounded = select_bounded_watchlist(filtered, previous_symbols, policy, clusters)
            self._update_watchlist(bounded)
        except Exception as e:
            logger.error(f"Bounded watchlist selection failed; watchlist.csv left unchanged: {e}")

        return top_picks

    def _update_watchlist(self, top_picks):
        if not top_picks:
            logger.warning("No stocks passed the screener filter. Watchlist unchanged.")
            return
            
        logger.info(f"Overwriting watchlist.csv with top {len(top_picks)} candidates.")
        with open(self.watchlist_file, 'w', newline='') as f:
            writer = csv.writer(f)
            # Ensure headers match the expected format
            writer.writerow(['Token', 'Symbol', 'Exchange'])
            for pick in top_picks:
                writer.writerow([pick['token'], pick['symbol'], pick['exchange']])
