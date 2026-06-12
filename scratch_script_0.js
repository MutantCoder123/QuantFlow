
        const SERVER_IP = '192.168.29.123:8000';
        const isLocalFile = window.location.protocol === 'file:';
        const wsHost = isLocalFile ? SERVER_IP : window.location.host;
        const apiHost = isLocalFile ? SERVER_IP : window.location.host;
        
        const wsProtocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
        const wsUri = `ws://${wsHost}/ws`;
        const apiBase = isLocalFile ? `http://${apiHost}` : '';
        let socket;

        // Global Cache for the Modal
        window.latestGlobalState = {};

        function connect() {
            socket = new WebSocket(wsUri);
            const statusDot = document.getElementById('status-dot');
            const statusText = document.getElementById('status-text');

            socket.onopen = () => {
                statusDot.className = 'w-2.5 h-2.5 rounded-full bg-emerald-500 shadow-[0_0_8px_#10b981]';
                statusText.innerText = 'Connected Live';
                statusText.className = 'text-xs font-semibold text-emerald-400';
            };

            socket.onmessage = (event) => {
                const payload = JSON.parse(event.data);
                updateMacroWeather(payload.macro_state);
                updateMarketMatrix(payload.global_state);
                // 4. Update Market Breadth
                if (payload.macro_state && payload.macro_state.ad_ratio !== undefined) {
                    const adRatio = parseFloat(payload.macro_state.ad_ratio);
                    const breadthElem = document.getElementById('breadth-val');
                    breadthElem.innerText = adRatio.toFixed(2);
                    breadthElem.className = adRatio >= 1.0 ? 'text-2xl font-bold font-mono-custom text-emerald-400' : 'text-2xl font-bold font-mono-custom text-rose-400';
                }
                
                // MOCK DATA FALLBACK FOR GLOBAL MARKET CONTEXT
                  if (!payload.global_market_context) {
                      payload.global_market_context = {
                          summary: "Global markets are showing strong resilience with cooling inflation data out of the US and steady domestic inflows in Indian markets. FII selling pressure seems to have absorbed. Key focus remains on upcoming Fed commentary and RBI policy alignment.",
                          sentiment: "BULLISH",
                          timestamp: Math.floor(Date.now() / 1000)
                      };
                  }
                  
                  // 5. Update Global Market Context
                if (payload.global_market_context) {
                    const ctx = payload.global_market_context;
                    const badge = document.getElementById('macro-sentiment-badge');
                    const summary = document.getElementById('macro-summary');
                    const timeElem = document.getElementById('macro-time');
                    
                    if (summary) summary.innerText = ctx.summary || "No summary available.";
                    if (badge) badge.innerText = ctx.sentiment || "NEUTRAL";
                    
                    if (ctx.timestamp && timeElem) {
                        timeElem.innerText = "Updated: " + new Date(ctx.timestamp * 1000).toLocaleTimeString();
                    }
                    
                    let bgClass = 'bg-slate-800', textClass = 'text-slate-400', borderClass = 'border-slate-700';
                    if (ctx.sentiment === 'BULLISH') {
                        bgClass = 'bg-emerald-500/20'; textClass = 'text-emerald-400'; borderClass = 'border-emerald-500/30';
                    } else if (ctx.sentiment === 'BEARISH') {
                        bgClass = 'bg-rose-500/20'; textClass = 'text-rose-400'; borderClass = 'border-rose-500/30';
                    } else if (ctx.sentiment === 'MIXED') {
                        bgClass = 'bg-purple-500/20'; textClass = 'text-purple-400'; borderClass = 'border-purple-500/30';
                    }
                    if (badge) badge.className = `px-2 py-0.5 text-[10px] font-bold rounded border ${bgClass} ${textClass} ${borderClass}`;
                }
                
                // 6. Update AI Intraday Playbook
                if (payload.dashboard_intraday_plays) {
                    const plays = payload.dashboard_intraday_plays;
                    const items = Array.isArray(plays) ? plays : plays.watchlist;
                    if (items && items.length > 0) {
                        // Hide spinner and re-enable button
                        const status = document.getElementById('screener-status');
                        if (status) { status.classList.add('hidden'); status.classList.remove('block'); }
                        const btn = document.getElementById('btn-run-discovery');
                        if (btn) { btn.disabled = false; btn.classList.remove('opacity-50'); }
                        
                        // Forward playbook data to the Discovery UI render function
                        renderDiscoveryCards(items);
                        // Hide empty state if needed
                        const empty = document.getElementById('screener-empty');
                        if (empty) empty.classList.add('hidden');
                    } else {
                        const empty = document.getElementById('screener-empty');
                        if (empty) {
                            empty.classList.remove('hidden');
                            empty.innerHTML = `<p class="text-slate-500 font-mono-custom text-sm">Received playbook but no items found (Length: ${items ? items.length : 0}). Data: ${JSON.stringify(plays).substring(0, 50)}...</p><p class="text-slate-600 text-xs mt-2">Click 'Run Discovery Engine' to analyze the broader market.</p>`;
                        }
                    }
                }
            };

            socket.onclose = () => {
                statusDot.className = 'w-2.5 h-2.5 rounded-full bg-rose-500 shadow-[0_0_8px_#f43f5e] animate-pulse';
                statusText.innerText = 'Reconnecting...';
                statusText.className = 'text-xs font-semibold text-rose-400';
                setTimeout(connect, 3000);
            };

            socket.onerror = (err) => {
                console.error('WebSocket error: ', err);
                socket.close();
            };
        }

        function updateMacroWeather(macro) {
            if (!macro) return;
            
            // 1. Update PCR
            const pcrVal = parseFloat(macro.pcr || 1.0);
            document.getElementById('pcr-val').innerText = pcrVal.toFixed(4);
            const pcrBadge = document.getElementById('pcr-badge');
            if (pcrVal > 1.3) {
                pcrBadge.className = 'px-2 py-0.5 text-[10px] font-bold rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30';
                pcrBadge.innerText = 'Bullish';
            } else if (pcrVal < 0.7) {
                pcrBadge.className = 'px-2 py-0.5 text-[10px] font-bold rounded bg-rose-500/20 text-rose-400 border border-rose-500/30';
                pcrBadge.innerText = 'Bearish';
            } else {
                pcrBadge.className = 'px-2 py-0.5 text-[10px] font-bold rounded bg-slate-800 text-slate-400 border border-slate-700';
                pcrBadge.innerText = 'Neutral';
            }

            // 2. Update FII/DII
            const fiiNet = parseFloat(macro.fii_net || 0);
            const diiNet = parseFloat(macro.dii_net || 0);
            const netTotal = fiiNet + diiNet;
            const dateStr = macro.date || 'N/A';

            document.getElementById('fii-val').innerText = `₹${fiiNet.toLocaleString('en-IN')} Cr`;
            document.getElementById('fii-val').className = fiiNet >= 0 ? 'text-2xl font-bold font-mono-custom text-emerald-400' : 'text-2xl font-bold font-mono-custom text-rose-400';

            document.getElementById('dii-val').innerText = `₹${diiNet.toLocaleString('en-IN')} Cr`;
            document.getElementById('dii-val').className = diiNet >= 0 ? 'text-2xl font-bold font-mono-custom text-emerald-400' : 'text-2xl font-bold font-mono-custom text-rose-400';

            // 3. Update Net Total
            const netDir = document.getElementById('net-direction');
            if (netTotal > 0) {
                netDir.className = 'text-sm font-semibold text-emerald-400';
                netDir.innerText = `Net Flow: +₹${netTotal.toFixed(2)} Cr (Net Buyer)`;
            } else if (netTotal < 0) {
                netDir.className = 'text-sm font-semibold text-rose-400';
                netDir.innerText = `Net Flow: -₹${Math.abs(netTotal).toFixed(2)} Cr (Net Seller)`;
            } else {
                netDir.className = 'text-sm font-semibold text-slate-400';
                netDir.innerText = `Net Flow: ₹0.00 Cr (Neutral)`;
            }
            
            document.getElementById('macro-date').innerText = `Date: ${dateStr}`;
        }

        function updateMarketMatrix(globalState) {
            const tableBody = document.getElementById('matrix-body');
            
            if (!globalState || Object.keys(globalState).length === 0) {
                tableBody.innerHTML = `
                    <tr>
                        <td colspan="12" class="px-6 py-16 text-center text-slate-500">
                            <svg class="mx-auto h-8 w-8 animate-spin text-emerald-500 mb-3" fill="none" viewBox="0 0 24 24">
                                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                            </svg>
                            Connected! Waiting for market data ticks...
                        </td>
                    </tr>
                `;
                return;
            }
            
            // Cache the state globally for the modal
            window.latestGlobalState = globalState;

            // True DOM Diffing: Do NOT clear tableBody.innerHTML
            // Target and destroy the initial loading row if it exists
            const loadingRow = document.getElementById('loading-row');
            if (loadingRow) loadingRow.remove();

            const tokens = Object.keys(globalState).sort();
            
            tokens.forEach(token => {
                const payload = globalState[token];
                const symbol = payload.symbol || token.split('|').pop();
                const ltp = parseFloat(payload.ltp || 0).toFixed(2);
                
                // --- 1. OBI Visual Gauge Logic ---
                const rawObi = parseFloat(payload.obi || 0);
                const obiPct = Math.min(100, Math.abs(rawObi) * 100);
                let obiBarHTML = '';
                if (rawObi > 0) {
                    obiBarHTML = `
                        <div class="relative w-full h-1.5 bg-slate-800 rounded-full overflow-hidden flex items-center justify-center mt-1">
                            <div class="absolute w-px h-full bg-slate-600 z-10"></div>
                            <div class="absolute left-1/2 h-full bg-emerald-500 rounded-r shadow-[0_0_5px_#10b981]" style="width: ${obiPct/2}%"></div>
                        </div>
                        <div class="text-[10px] text-emerald-400 text-center mt-0.5">${rawObi.toFixed(4)}</div>
                    `;
                } else if (rawObi < 0) {
                    obiBarHTML = `
                        <div class="relative w-full h-1.5 bg-slate-800 rounded-full overflow-hidden flex items-center justify-center mt-1">
                            <div class="absolute w-px h-full bg-slate-600 z-10"></div>
                            <div class="absolute right-1/2 h-full bg-rose-500 rounded-l shadow-[0_0_5px_#f43f5e]" style="width: ${obiPct/2}%"></div>
                        </div>
                        <div class="text-[10px] text-rose-400 text-center mt-0.5">${rawObi.toFixed(4)}</div>
                    `;
                } else {
                    obiBarHTML = `
                        <div class="relative w-full h-1.5 bg-slate-800 rounded-full overflow-hidden flex items-center justify-center mt-1">
                            <div class="absolute w-px h-full bg-slate-600 z-10"></div>
                        </div>
                        <div class="text-[10px] text-slate-500 text-center mt-0.5">0.0000</div>
                    `;
                }

                // --- 2. CVD Pill Badge ---
                const cvd = parseInt(payload.cvd || 0);
                let cvdBadge = `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-slate-800 text-slate-300 border border-slate-700">${cvd.toLocaleString()}</span>`;
                if (cvd > 0) {
                    cvdBadge = `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">${cvd.toLocaleString()}</span>`;
                } else if (cvd < -10000) {
                    cvdBadge = `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-rose-500/20 text-rose-400 border border-rose-500/50 animate-pulse">${cvd.toLocaleString()}</span>`;
                } else if (cvd < 0) {
                    cvdBadge = `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-rose-500/10 text-rose-400 border border-rose-500/30">${cvd.toLocaleString()}</span>`;
                }

                // --- 3. POC Pill Badge ---
                const rawPocDist = parseFloat(payload.poc_distance_pct || 0);
                let pocBadge = `<span class="text-slate-300 text-xs">${rawPocDist.toFixed(3)}%</span>`;
                if (Math.abs(rawPocDist) < 0.1) {
                    pocBadge = `<span class="px-2 py-0.5 rounded-full text-[11px] font-extrabold bg-cyan-500/20 text-cyan-400 border border-cyan-500/40 shadow-[0_0_8px_rgba(6,182,212,0.2)]">${rawPocDist.toFixed(3)}%</span>`;
                }

                // --- 4. RSI Progress Bar ---
                const rawRsi = parseFloat(payload.rsi_5m || 50);
                const rsiPct = Math.max(0, Math.min(100, rawRsi));
                let rsiColor = 'bg-slate-500';
                let rsiTextColor = 'text-slate-300';
                if (rawRsi > 70) {
                    rsiColor = 'bg-emerald-500 shadow-[0_0_8px_#10b981]';
                    rsiTextColor = 'text-emerald-400 font-bold';
                } else if (rawRsi < 30) {
                    rsiColor = 'bg-rose-500 shadow-[0_0_8px_#f43f5e]';
                    rsiTextColor = 'text-rose-400 font-bold';
                }
                const rsiBarHTML = `
                    <div class="flex flex-col gap-1.5 items-end w-full px-2">
                        <span class="text-[11px] font-mono-custom ${rsiTextColor}">${rawRsi.toFixed(2)}</span>
                        <div class="w-full h-1.5 bg-slate-800/80 rounded-full overflow-hidden">
                            <div class="h-full rounded-full ${rsiColor} transition-all duration-300" style="width: ${rsiPct}%"></div>
                        </div>
                    </div>
                `;

                // --- 5. Candlestick Matrix Logic ---
                const cdls = payload.candlesticks || {};
                let activeCdl = "None";
                const validCdls = Object.keys(cdls).filter(k => k !== "active_patterns" && cdls[k] !== null);
                if (validCdls.length > 0) {
                    activeCdl = validCdls[validCdls.length - 1].replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
                }
                
                let cdlBadge = '<span class="text-slate-500 text-[11px] font-medium">None</span>';
                if (activeCdl !== "None") {
                    const cdlLower = activeCdl.toLowerCase();
                    const isBullish = cdlLower.includes('bullish') || (cdlLower.includes('hammer') || cdlLower.includes('morning')) && !cdlLower.includes('evening') && !cdlLower.includes('shooting');
                    const isBearish = cdlLower.includes('bearish') || cdlLower.includes('shooting') || cdlLower.includes('evening');
                    
                    if (isBullish) {
                        cdlBadge = `<span class="px-2.5 py-1 rounded-full text-[10px] uppercase font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 shadow-[0_0_8px_rgba(16,185,129,0.05)]">${activeCdl}</span>`;
                    } else if (isBearish) {
                        cdlBadge = `<span class="px-2.5 py-1 rounded-full text-[10px] uppercase font-bold bg-rose-500/10 text-rose-400 border border-rose-500/20 shadow-[0_0_8px_rgba(244,63,94,0.05)]">${activeCdl}</span>`;
                    } else {
                        cdlBadge = `<span class="px-2.5 py-1 rounded-full text-[10px] uppercase font-bold bg-slate-800 text-slate-300 border border-slate-700">${activeCdl}</span>`;
                    }
                }

                // --- Action Button ---
                const actionBtn = `
                    <div class="flex flex-col gap-1.5">
                        <button onclick="openPositionModal('${token}')" class="px-3 py-1 text-[10px] font-bold uppercase tracking-wider rounded bg-emerald-600/20 text-emerald-300 hover:bg-emerald-500/40 hover:text-white transition-colors border border-emerald-500/30 shadow">📝 Pos</button>
                        <button onclick="openJsonModal('${token}')" class="px-3 py-1 text-[10px] font-bold uppercase tracking-wider rounded bg-indigo-600/20 text-indigo-300 hover:bg-indigo-500/40 hover:text-white transition-colors border border-indigo-500/30 shadow">🤖 View JSON</button>
                    </div>
                `;

                // --- Phase 5.2: Derivatives ---
                const stockPcr = parseFloat(payload.stock_pcr || 1.0);
                const maxPain = payload.max_pain_price ? parseFloat(payload.max_pain_price).toFixed(2) : "N/A";
                
                let pcrClass = 'text-slate-300';
                if (stockPcr > 1.2) pcrClass = 'text-emerald-400 font-bold';
                else if (stockPcr < 0.8) pcrClass = 'text-rose-400 font-bold';
                
                const pcrBadge = `<span class="${pcrClass}">${stockPcr.toFixed(2)}</span>`;
                const maxPainBadge = `<span class="text-cyan-400 font-bold font-mono-custom bg-cyan-950/30 px-2 py-0.5 rounded border border-cyan-500/20">${maxPain}</span>`;

                // --- Phase 6: Statistical Alpha ---
                const ivr = parseFloat(payload.ivr || 0.0).toFixed(2);
                let ivrClass = 'text-slate-300';
                if (ivr < 20) ivrClass = 'text-emerald-400 font-bold'; // Low IV
                else if (ivr > 80) ivrClass = 'text-rose-400 font-bold'; // High IV
                const ivrBadge = `<span class="${ivrClass}">${ivr}</span>`;

                const compRS = parseFloat(payload.comparative_rs || 0.0).toFixed(2);
                let rsClass = 'text-slate-300';
                if (compRS > 0) rsClass = 'text-emerald-400 font-bold';
                else if (compRS < 0) rsClass = 'text-rose-400 font-bold';
                const rsBadge = `<span class="${rsClass}">${compRS}</span>`;

                // Render Row using True DOM Diffing
                let existingRow = document.getElementById(`row-${token}`);
                if (existingRow) {
                    existingRow.cells[0].innerHTML = `
                        <span>${symbol}</span>
                        <span class="text-[10px] text-slate-500 font-normal">Token #${token}</span>
                    `;
                    existingRow.cells[1].innerText = ltp;
                    existingRow.cells[2].innerHTML = `<div class="px-2">${obiBarHTML}</div>`;
                    existingRow.cells[3].innerHTML = cvdBadge;
                    existingRow.cells[4].innerHTML = pocBadge;
                    existingRow.cells[5].innerHTML = rsiBarHTML;
                    existingRow.cells[6].innerHTML = pcrBadge;
                    existingRow.cells[7].innerHTML = ivrBadge;
                    existingRow.cells[8].innerHTML = maxPainBadge;
                    existingRow.cells[9].innerHTML = rsBadge;
                    existingRow.cells[10].innerHTML = cdlBadge;
                    existingRow.cells[11].innerHTML = actionBtn;
                } else {
                    const tr = document.createElement('tr');
                    tr.id = `row-${token}`;
                    tr.className = 'border-b border-slate-800/40 hover:bg-slate-800/25 transition-colors duration-150';
                    tr.innerHTML = `
                        <td class="px-5 py-4 whitespace-nowrap text-sm font-semibold text-indigo-400 flex flex-col">
                            <span>${symbol}</span>
                            <span class="text-[10px] text-slate-500 font-normal">Token #${token}</span>
                        </td>
                        <td class="px-5 py-4 whitespace-nowrap text-sm font-mono-custom text-white text-right font-medium">${ltp}</td>
                        <td class="px-5 py-4 whitespace-nowrap align-middle w-32">
                            <div class="px-2">${obiBarHTML}</div>
                        </td>
                        <td class="px-5 py-4 whitespace-nowrap text-right align-middle font-mono-custom">${cvdBadge}</td>
                        <td class="px-5 py-4 whitespace-nowrap text-right align-middle font-mono-custom">${pocBadge}</td>
                        <td class="px-5 py-4 whitespace-nowrap align-middle w-32">${rsiBarHTML}</td>
                        <td class="px-5 py-4 whitespace-nowrap text-center align-middle font-mono-custom">${pcrBadge}</td>
                        <td class="px-5 py-4 whitespace-nowrap text-center align-middle font-mono-custom">${ivrBadge}</td>
                        <td class="px-5 py-4 whitespace-nowrap text-center align-middle">${maxPainBadge}</td>
                        <td class="px-5 py-4 whitespace-nowrap text-center align-middle font-mono-custom">${rsBadge}</td>
                        <td class="px-5 py-4 whitespace-nowrap text-center align-middle">${cdlBadge}</td>
                        <td class="px-5 py-4 whitespace-nowrap text-center align-middle">${actionBtn}</td>
                    `;
                    tableBody.appendChild(tr);
                }
            });
        }

        // Modal Logic Functions
        function openJsonModal(token) {
            if (!window.latestGlobalState || !window.latestGlobalState[token]) return;
            const payload = window.latestGlobalState[token];
            const symbol = payload.symbol || token.split('|').pop();
            
            // Store globally for the clipboard function
            window.currentViewingToken = token;
            
            // Deep copy payload so we don't pollute the live streaming object
            const payloadCopy = JSON.parse(JSON.stringify(payload));
            
            // Append User Position if it exists in memory
            try {
                const allPos = JSON.parse(localStorage.getItem('user_positions')) || {};
                if (allPos[token]) {
                    payloadCopy.user_position = allPos[token];
                }
            } catch (e) {
                console.error("Failed to append user position:", e);
            }
            
            document.getElementById('modal-title').innerHTML = `<span class="text-xl">🤖</span> AI Reasoning: ${symbol}`;
            document.getElementById('modal-code-block').innerText = JSON.stringify(payloadCopy, null, 2);
                        switchModalTab('reasoning');
            const catalystBlock = document.getElementById('catalyst-report-block');
            if (payloadCopy.latest_catalyst) {
                catalystBlock.innerText = "Current Cached Catalyst Summary:\n\n" + payloadCopy.latest_catalyst;
            } else {
                catalystBlock.innerText = "No cached news catalyst available for this asset. Click 'Force Fetch News' to query live API.";
            }
            document.getElementById('llm-modal').classList.remove('hidden');

            // Phase 8 Reset UI state & fetch existing report
            document.getElementById('reasoning-report-block').innerText = "Awaiting trigger...";
            document.getElementById('toggle-auto-analyze').checked = false;
            if (window.autoAnalyzeIntervalId) { 
                clearInterval(window.autoAnalyzeIntervalId); 
                window.autoAnalyzeIntervalId = null; 
            }
            
            fetch(`${apiBase}/api/reasoning/report/${symbol}`)
                .then(r => r.json())
                .then(data => {
                    if (data.report) document.getElementById('reasoning-report-block').innerText = data.report;
                    document.getElementById('toggle-auto-analyze').checked = data.is_active;
                    if (data.is_active) {
                        window.autoAnalyzeIntervalId = setInterval(async () => {
                            const res = await fetch(`${apiBase}/api/reasoning/report/${symbol}`);
                            const d = await res.json();
                            if (d.report) document.getElementById('reasoning-report-block').innerText = d.report;
                            document.getElementById('toggle-auto-analyze').checked = d.is_active;
                            if(!d.is_active) { clearInterval(window.autoAnalyzeIntervalId); window.autoAnalyzeIntervalId=null; }
                        }, 5000);
                    }
                }).catch(e => console.error("Fetch report error", e));
        }

        // --- Modal Tabs Logic ---
        function switchModalTab(tabName) {
            ['reasoning', 'catalyst', 'telemetry'].forEach(t => {
                document.getElementById(`tab-content-${t}`).classList.add('hidden');
                document.getElementById(`tab-content-${t}`).classList.remove('flex');
                
                const btn = document.getElementById(`tab-btn-${t}`);
                btn.className = "px-4 py-2 text-xs font-bold uppercase tracking-wider rounded-t-lg bg-transparent text-slate-500 hover:text-slate-300 transition-colors";
            });
            
            document.getElementById(`tab-content-${tabName}`).classList.remove('hidden');
            document.getElementById(`tab-content-${tabName}`).classList.add('flex');
            
            const activeBtn = document.getElementById(`tab-btn-${tabName}`);
            activeBtn.className = "px-4 py-2 text-xs font-bold uppercase tracking-wider rounded-t-lg bg-slate-800 text-indigo-400 border-t border-l border-r border-slate-700 transition-colors";
        }

        // --- Force News Fetch ---
        async function forceFetchNews() {
            if (!window.currentViewingToken) return;
            const token = window.currentViewingToken;
            const btn = document.getElementById('btn-force-news');
            const block = document.getElementById('catalyst-report-block');
            const modelSelect = document.getElementById('reasoning-model-select');
            const model = modelSelect ? modelSelect.value : 'gemini-2.5-flash';
            
            btn.innerText = "⏳ Fetching...";
            btn.disabled = true;
            block.innerText = "Querying live API for news...";
            
            try {
                // Get symbol from latestGlobalState
                const symbol = window.latestGlobalState[token]?.symbol || token;
                
                const res = await fetch(`${apiBase}/api/news/fetch/${symbol}`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({model: model})
                });
                const data = await res.json();
                
                if (data.status === 'success') {
                    // Prettify output
                    let out = `--- RAW NEWS FETCHED ---\n`;
                    if (data.raw_news && data.raw_news.length > 0) {
                        data.raw_news.forEach((n, i) => {
                            out += `\n[Article ${i+1}] ${n.heading}\n${n.summary}\nUrl: ${n.url}\n`;
                        });
                    } else {
                        out += `\nNo news articles found for ${symbol}.\n`;
                    }
                    
                    out += `\n--- AI CATALYST SUMMARY ---\n`;
                    out += `\n${data.catalyst_summary || "No summary generated."}\n`;
                    block.innerText = out;
                } else {
                    block.innerText = `Error: ${data.message || 'Unknown error'}`;
                }
            } catch (e) {
                block.innerText = `Failed to fetch news: ${e.message}`;
            } finally {
                btn.innerHTML = "📰 Force Fetch News";
                btn.disabled = false;
            }
        }

        function closeJsonModal() {
            document.getElementById('llm-modal').classList.add('hidden');
        }


                function getUserIntent() {
            const action = document.getElementById('intent-action').value;
            if (action === "None") return null;
            const type = document.getElementById('intent-type').value;
            const advice = document.getElementById('intent-advice').value;
            const qty = parseFloat(document.getElementById('intent-qty').value) || 0;
            const price = parseFloat(document.getElementById('intent-price').value) || 0;
            return { action: action, type: type, quantity: qty, price: price, advice: advice };
        }

        function copyToClipboard() {
            const codeBlock = document.getElementById('modal-code-block').innerText;
            const currentPrompt = localStorage.getItem('llm_system_prompt') || DEFAULT_SYSTEM_PROMPT;
            
            let statusText = '';
            if (window.currentViewingToken) {
                try {
                    const allPos = JSON.parse(localStorage.getItem('user_positions')) || {};
                    const myPos = allPos[window.currentViewingToken];
                    if (myPos) {
                        const action = myPos.direction === 'Long' ? 'Holding' : 'Short';
                        statusText = `My Status: ${action} ${myPos.mode}, ${myPos.quantity} qty entered at ${myPos.entry_price}.\n\n`;
                    }
                } catch (e) {
                    console.error("Failed to parse position for clipboard:", e);
                }
            }

            
            let intentText = '';
            const intent = getUserIntent();
            if (intent) {
                intentText = `Proposed Trade Intent: ${intent.action} (${intent.type}) | Qty: ${intent.quantity} | Price: ${intent.price}\nAdvice/Note: ${intent.advice || 'None'}\n\n`;
            }

            const finalClipboardText = `${statusText}${intentText}${currentPrompt}\n\n--- RAW TELEMETRY DATA ---\n\n${codeBlock}`;

            // Helper to update UI
            const updateUI = () => {
                const btn = document.getElementById('modal-copy-btn');
                const originalText = btn.innerText;
                btn.innerText = 'Copied!';
                btn.classList.replace('bg-indigo-600', 'bg-emerald-600');
                btn.classList.replace('hover:bg-indigo-500', 'hover:bg-emerald-500');
                
                setTimeout(() => {
                    btn.innerText = originalText;
                    btn.classList.replace('bg-emerald-600', 'bg-indigo-600');
                    btn.classList.replace('hover:bg-emerald-500', 'hover:bg-indigo-500');
                }, 2000);
            };

            // Modern secure clipboard API (HTTPS or Localhost only)
            if (navigator.clipboard && window.isSecureContext) {
                navigator.clipboard.writeText(finalClipboardText).then(updateUI).catch(err => {
                    console.error('Failed to copy text: ', err);
                });
            } else {
                // Fallback for HTTP over LAN (e.g. 192.168.x.x)
                let textArea = document.createElement("textarea");
                textArea.value = finalClipboardText;
                textArea.style.position = "fixed";
                textArea.style.left = "-999999px";
                textArea.style.top = "-999999px";
                document.body.appendChild(textArea);
                textArea.focus();
                textArea.select();
                try {
                    document.execCommand('copy');
                    updateUI();
                } catch (err) {
                    console.error('Fallback copy failed: ', err);
                    alert("Copy failed. Your browser requires HTTPS to copy to clipboard.");
                }
                textArea.remove();
            }
        }

        // --- Phase 8: Reasoning Engine Control ---
        window.autoAnalyzeIntervalId = null;

        async function triggerInstantAnalysis() {
            const token = window.currentViewingToken;
            if (!token) return;
            
            const payload = window.latestGlobalState[token];
            const symbol = payload.symbol || token.split('|').pop();
            const model = document.getElementById('reasoning-model-select').value;
            const prompt = localStorage.getItem('llm_system_prompt') || DEFAULT_SYSTEM_PROMPT;
            
            const allPos = JSON.parse(localStorage.getItem('user_positions')) || {};
            const userPosition = allPos[token] || null;
            
            const btn = document.getElementById('btn-instant-analyze');
            const reportBlock = document.getElementById('reasoning-report-block');
            
            btn.disabled = true;
            btn.innerHTML = `<svg class="animate-spin -ml-1 mr-2 h-4 w-4 text-white inline" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> Analyzing...`;
            reportBlock.innerText = "Querying Neuro-Symbolic LLM...";
            reportBlock.classList.add('animate-pulse');

            try {
                const res = await fetch(`${apiBase}/api/reasoning/instant/${symbol}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ model: model, prompt: prompt, user_position: userPosition })
                });
                const data = await res.json();
                reportBlock.classList.remove('animate-pulse');
                reportBlock.innerText = data.report || "No report generated.";
            } catch (err) {
                console.error(err);
                reportBlock.classList.remove('animate-pulse');
                reportBlock.innerText = "Error reaching AI API.";
            } finally {
                btn.disabled = false;
                btn.innerHTML = `⚡ Instant Analyze`;
            }
        }

        async function toggleAutoAnalyze() {
            const token = window.currentViewingToken;
            if (!token) return;
            const payload = window.latestGlobalState[token];
            const symbol = payload.symbol || token.split('|').pop();
            
            const isChecked = document.getElementById('toggle-auto-analyze').checked;
            const intervalSecs = parseInt(document.getElementById('auto-analyze-interval').value) || 90;
            const model = document.getElementById('reasoning-model-select').value;
            const prompt = localStorage.getItem('llm_system_prompt') || DEFAULT_SYSTEM_PROMPT;
            
            const allPos = JSON.parse(localStorage.getItem('user_positions')) || {};
            const userPosition = allPos[token] || null;
            
            if (isChecked) {
                fetch(`${apiBase}/api/reasoning/loop/start`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ symbol: symbol, interval: intervalSecs, model: model, prompt: prompt, user_position: userPosition })
                });
                
                if (window.autoAnalyzeIntervalId) clearInterval(window.autoAnalyzeIntervalId);
                window.autoAnalyzeIntervalId = setInterval(async () => {
                    if (!window.currentViewingToken) return;
                    try {
                        const res = await fetch(`${apiBase}/api/reasoning/report/${symbol}`);
                        const data = await res.json();
                        if (data.status === 'success') {
                            document.getElementById('reasoning-report-block').innerText = data.report;
                            document.getElementById('toggle-auto-analyze').checked = data.is_active;
                        }
                    } catch (e) { console.error("Poll error", e); }
                }, 5000);
            } else {
                fetch(`${apiBase}/api/reasoning/loop/stop`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ symbol: symbol })
                });
                
                if (window.autoAnalyzeIntervalId) {
                    clearInterval(window.autoAnalyzeIntervalId);
                    window.autoAnalyzeIntervalId = null;
                }
            }
        }

        // --- Phase 9: Asynchronous Alerting Matrix ---
        function toggleAlertTray() {
            const tray = document.getElementById('alert-tray');
            tray.classList.toggle('hidden');
            if (!tray.classList.contains('hidden')) {
                fetchAlertHistory();
            }
        }

        async function fetchUnreadCount() {
            try {
                const res = await fetch(`${apiBase}/api/alerts/unread`);
                const data = await res.json();
                const badge = document.getElementById('alert-badge');
                if (data.count > 0) {
                    badge.innerText = data.count > 99 ? '99+' : data.count;
                    badge.classList.remove('hidden');
                } else {
                    badge.classList.add('hidden');
                }
            } catch (e) { console.error("Poll unread alerts error", e); }
        }

        async function fetchAlertHistory() {
            try {
                const res = await fetch(`${apiBase}/api/alerts/history`);
                const data = await res.json();
                const trayBody = document.getElementById('alert-tray-body');
                trayBody.innerHTML = '';
                
                if (!data.alerts || data.alerts.length === 0) {
                    trayBody.innerHTML = `<div class="text-center text-xs text-slate-500 p-4">No alerts right now.</div>`;
                    return;
                }
                
                data.alerts.forEach(alert => {
                    const isUnread = !alert.read;
                    const dot = isUnread ? `<div class="w-2 h-2 rounded-full bg-rose-500 mt-1"></div>` : '';
                    const timeStr = new Date(alert.timestamp * 1000).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'});
                    
                    const el = document.createElement('div');
                    el.className = `p-3 rounded-md cursor-pointer transition-colors border ${isUnread ? 'bg-slate-800 border-slate-700 hover:bg-slate-700' : 'bg-slate-900/50 border-transparent hover:bg-slate-800'}`;
                    el.onclick = () => openAlertModal(alert.id, alert.symbol);
                    el.innerHTML = `
                        <div class="flex justify-between items-start">
                            <div class="flex gap-2">
                                ${dot}
                                <div>
                                    <h5 class="text-xs font-bold ${alert.verdict.includes('Buy') ? 'text-emerald-400' : 'text-rose-400'}">${alert.symbol}</h5>
                                    <p class="text-[10px] text-slate-300 mt-1">${alert.verdict} <span class="text-slate-500">(Score: ${alert.score})</span></p>
                                </div>
                            </div>
                            <span class="text-[9px] text-slate-500 whitespace-nowrap">${timeStr}</span>
                        </div>
                    `;
                    trayBody.appendChild(el);
                });
            } catch (e) { console.error("Fetch alert history error", e); }
        }

        async function openAlertModal(alertId, symbol) {
            document.getElementById('alert-tray').classList.add('hidden');
            try {
                // Mark as read
                await fetch(`${apiBase}/api/alerts/mark-read/${alertId}`, { method: 'POST' });
                fetchUnreadCount(); // update badge
                
                // Lookup token from global state if we can
                let token = null;
                for (const t in window.latestGlobalState) {
                    const p = window.latestGlobalState[t];
                    if (p.symbol === symbol || (p.symbol && p.symbol.split('-')[0] === symbol)) {
                        token = t; break;
                    }
                }
                
                if (token) {
                    openJsonModal(token); // This will fetch the latest report too!
                } else {
                    alert("Could not locate active stream for symbol: " + symbol);
                }
            } catch (e) { console.error("Open alert error", e); }
        }

        // Start Alert Poller
        setInterval(fetchUnreadCount, 10000);
        setTimeout(fetchUnreadCount, 1000);

        // --- Phase 0: Screener Logic ---
        const btnScreener = document.getElementById('btn-screener');
        if (btnScreener) {
            btnScreener.addEventListener('click', async () => {
            const loadingModal = document.getElementById('loading-modal');
            const resultsModal = document.getElementById('results-modal');
            const tbody = document.getElementById('results-tbody');
            
            loadingModal.classList.remove('hidden');
            
            try {
                const response = await fetch(`${apiBase}/api/run-screener`, { method: 'POST' });
                const result = await response.json();
                
                tbody.innerHTML = '';
                if (result.status === 'success' && result.data) {
                    result.data.forEach(item => {
                        const tr = document.createElement('tr');
                        tr.innerHTML = `
                            <td class="px-4 py-3 text-indigo-400 font-semibold">${item.symbol}</td>
                            <td class="px-4 py-3 text-right text-emerald-400 font-mono-custom">${parseFloat(item.rs).toFixed(2)}%</td>
                            <td class="px-4 py-3 text-right text-white font-mono-custom">${parseFloat(item.ivr).toFixed(2)}</td>
                        `;
                        tbody.appendChild(tr);
                    });
                }
                
                loadingModal.classList.add('hidden');
                resultsModal.classList.remove('hidden');
                
            } catch (error) {
                console.error("Screener failed:", error);
                loadingModal.classList.add('hidden');
                alert("Screener failed to execute. Check terminal logs.");
            }
        });
        }

        function closeResultsModal() {
            document.getElementById('results-modal').classList.add('hidden');
        }

        // Initialize WebSocket connection
        connect();
    