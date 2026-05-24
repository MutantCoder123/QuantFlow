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

    @classmethod
    def update_state(cls, token, payload):
        cls.active_states[token] = payload

    async def render_loop(self):
        # Rich UI disabled to allow standard logging and error visibility
        while True:
            await asyncio.sleep(5)

