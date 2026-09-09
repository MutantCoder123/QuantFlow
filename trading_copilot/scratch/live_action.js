// Live Action Global State
window.dismissedLiveReports = {}; // { "RELIANCE": "report_hash_or_text" }

function startLiveActionPolling() {
    if (window.liveActionPollInterval) clearInterval(window.liveActionPollInterval);
    pollLiveActionCards();
    window.liveActionPollInterval = setInterval(pollLiveActionCards, 3000);
}

function stopLiveActionPolling() {
    if (window.liveActionPollInterval) clearInterval(window.liveActionPollInterval);
}

async function pollLiveActionCards() {
    if (document.getElementById('tab-live-action').classList.contains('hidden')) return;

    try {
        const res = await fetch(`${apiBase}/api/reasoning/all_reports`);
        const data = await res.json();
        if (data.status === 'success' && data.reports) {
            renderLiveActionGrid(data.reports);
        }
    } catch (e) {
        console.error("Failed to poll live action reports", e);
    }
}

function renderLiveActionGrid(reportsObj) {
    const grid = document.getElementById('live-action-grid');
    
    let cardsData = [];
    for (const [symbol, text] of Object.entries(reportsObj)) {
        if (window.dismissedLiveReports[symbol] === text) continue; // Hidden by Accept

        const jsonMatch = text.match(/```(?:json)?\s*(\{[\s\S]*?\})\s*```/);
        if (jsonMatch) {
            try {
                const actionData = JSON.parse(jsonMatch[1]);
                cardsData.push({ symbol, text, actionData });
            } catch (e) {}
        }
    }

    // Sort by Priority Score descending
    cardsData.sort((a, b) => {
        const scoreA = a.actionData.Priority_Score || 0;
        const scoreB = b.actionData.Priority_Score || 0;
        return scoreB - scoreA;
    });

    if (cardsData.length === 0) {
        grid.innerHTML = `<div class="col-span-full text-center text-slate-500 py-12">No actionable setups detected yet. Turn on Auto-Analyze or hit Instant Analyze on a stock.</div>`;
        return;
    }

    let html = '';
    cardsData.forEach(card => {
        const d = card.actionData;
        const score = d.Priority_Score || 0;
        const isHighPriority = score > 6;
        const borderGlow = isHighPriority ? 'border-emerald-500 shadow-[0_0_15px_rgba(16,185,129,0.3)]' : 'border-slate-700/80';
        
        const actionClass = d.Action === 'Short' ? 'bg-rose-900/40 text-rose-300 border-rose-700/50' : 
                           (d.Action === 'Wait' || d.Action === 'Hold' ? 'bg-amber-900/40 text-amber-300 border-amber-700/50' : 
                           'bg-emerald-900/40 text-emerald-300 border-emerald-700/50');

        // Escape text for onclick
        const escapedText = card.text.replace(/'/g, "\\'").replace(/"/g, '&quot;').replace(/\n/g, '\\n');

        html += `
        <div class="bg-slate-900 border rounded-xl p-4 relative overflow-hidden flex flex-col shrink-0 ${borderGlow} transition-all">
            <div class="flex justify-between items-start mb-3">
                <div class="flex items-center gap-2">
                    <h3 class="text-xl font-bold text-white tracking-tight">${card.symbol}</h3>
                    <span class="px-2 py-0.5 text-xs font-bold rounded ${isHighPriority ? 'bg-emerald-900/60 text-emerald-400 border border-emerald-700' : 'bg-slate-800 text-slate-400 border border-slate-700'}">Pri: ${score}</span>
                </div>
                <span class="px-2 py-0.5 text-xs font-bold rounded ${actionClass} border uppercase">${d.Action || 'N/A'}</span>
            </div>

            <div class="grid grid-cols-2 gap-2 mb-4 mt-2">
                <div class="bg-slate-800/50 p-2 rounded border border-slate-700/50 text-center">
                    <div class="text-xs text-slate-400 uppercase tracking-wider mb-1">Entry</div>
                    <div class="text-sm font-bold text-indigo-400">${d.Entry_Target_Price || 'N/A'}</div>
                </div>
                <div class="bg-slate-800/50 p-2 rounded border border-slate-700/50 text-center">
                    <div class="text-xs text-slate-400 uppercase tracking-wider mb-1">Target</div>
                    <div class="text-sm font-bold text-emerald-400">${d.Exit_Target_Price || 'N/A'}</div>
                </div>
                <div class="bg-slate-800/50 p-2 rounded border border-slate-700/50 text-center col-span-2">
                    <div class="text-xs text-slate-400 uppercase tracking-wider mb-1">Stop Loss</div>
                    <div class="text-sm font-bold text-rose-400">${d.Stoploss || 'N/A'}</div>
                </div>
            </div>

            <div class="flex justify-between items-center gap-2 mt-auto pt-2 border-t border-slate-800">
                <button onclick="forceReasoning('${card.symbol}')" class="flex-1 bg-indigo-600/20 hover:bg-indigo-600/40 text-indigo-300 border border-indigo-700/50 py-1.5 rounded text-xs font-bold transition-colors">Instant</button>
                <button onclick="openLiveSettingsModal('${card.symbol}')" class="w-8 h-8 flex items-center justify-center bg-slate-800 hover:bg-slate-700 text-slate-400 border border-slate-700 rounded transition-colors" title="Settings">⚙️</button>
                <button onclick="showLiveAnalysisModal('${escapedText}')" class="flex-1 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 py-1.5 rounded text-xs font-bold transition-colors">Analysis</button>
            </div>
            <div class="flex justify-between items-center gap-2 mt-2">
                <button onclick="openJsonModal('${card.symbol}')" class="flex-1 bg-slate-800 hover:bg-slate-700 text-slate-400 border border-slate-700 py-1.5 rounded text-[10px] uppercase font-bold transition-colors">Position</button>
                <button onclick="acceptLiveActionCard('${card.symbol}', '${escapedText}')" class="flex-1 bg-emerald-600/20 hover:bg-emerald-600/40 text-emerald-400 border border-emerald-700/50 py-1.5 rounded text-[10px] uppercase font-bold transition-colors">Accept</button>
            </div>
        </div>
        `;
    });

    grid.innerHTML = html;
}

function showLiveAnalysisModal(text) {
    const stripped = text.replace(/```(?:json)?[\s\S]*?```/, '').trim();
    alert("AI Analysis:\\n\\n" + stripped);
}

function acceptLiveActionCard(symbol, text) {
    window.dismissedLiveReports[symbol] = text;
    if (window.latestGlobalState && window.latestGlobalState[symbol]) {
        delete window.latestGlobalState[symbol].user_position;
    }
    pollLiveActionCards();
}

function openLiveSettingsModal(symbol) {
    const freq = prompt(`Enter Auto-Analyze frequency in seconds for ${symbol}:`, "60");
    if (freq) {
        fetch(`${apiBase}/api/reasoning/loop/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: symbol, interval: parseInt(freq) })
        });
        alert(`Started auto-analyze for ${symbol} every ${freq} seconds.`);
    }
}

function toggleGlobalAutoAnalyze() {
    const isChecked = document.getElementById('global-auto-analyze').checked;
    const freq = document.getElementById('global-analyze-interval').value;
    const symbols = window.globalWatchlistSymbols || Object.keys(window.latestGlobalState || {});
    
    symbols.forEach(sym => {
        if (isChecked) {
            fetch(`${apiBase}/api/reasoning/loop/start`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol: sym, interval: parseInt(freq) })
            });
        } else {
            fetch(`${apiBase}/api/reasoning/loop/stop`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol: sym })
            });
        }
    });
}
