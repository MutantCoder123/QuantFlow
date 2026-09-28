"""Paper trading must be structurally unable to place a real order.

Scans every module in trading_copilot/paper/ for imports of broker order
APIs (Upstox OrderApi / place_order, Angel One SmartConnect) or calls that
look like order placement. A paper module that needs one of these is a
design error, not a missing feature.
"""
import ast
from pathlib import Path

PAPER = Path(__file__).resolve().parents[1] / "trading_copilot" / "paper"
FORBIDDEN_MODULES = ("upstox_client", "smartapi", "SmartApi", "kiteconnect")
FORBIDDEN_NAMES = ("OrderApi", "OrderApiV3", "place_order", "placeOrder", "SmartConnect", "modify_order")


def test_paper_package_imports_no_broker_order_api():
    offences = []
    for path in PAPER.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", None) or ""
                names = [a.name for a in node.names]
                if any(m in mod for m in FORBIDDEN_MODULES) or any(
                        any(m in n for m in FORBIDDEN_MODULES) for n in names):
                    offences.append(f"{path.name}: import {mod or names}")
            if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
                offences.append(f"{path.name}: {node.id}")
            if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
                offences.append(f"{path.name}: .{node.attr}")
    assert not offences, offences
    assert list(PAPER.glob("*.py")), "paper package not found"
