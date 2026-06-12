import asyncio
from rich.live import Live
from rich.table import Table
from rich.console import Console, Group
from rich.panel import Panel
from derivatives_engine import OptionsAnalyzer
from macro_eod_engine import InstitutionalFlowTracker

console = Console()

class TerminalDashboard:
    active_states = {}
    global_market_context = None
    dashboard_intraday_plays = None

    @classmethod
    def update_state(cls, token, payload):
        existing = cls.active_states.get(token, {})
        # Preserve derivatives worker injected fields!
        if 'stock_pcr' in existing and 'stock_pcr' not in payload:
            payload['stock_pcr'] = existing['stock_pcr']
        if 'max_pain_price' in existing and 'max_pain_price' not in payload:
            payload['max_pain_price'] = existing['max_pain_price']
        if 'ivr' in existing and 'ivr' not in payload:
            payload['ivr'] = existing['ivr']
        if 'atm_iv' in existing and 'atm_iv' not in payload:
            payload['atm_iv'] = existing['atm_iv']
            
        cls.active_states[token] = payload
    async def render_loop(self):
        # Rich UI disabled to allow standard logging and error visibility
        while True:
            await asyncio.sleep(5)

