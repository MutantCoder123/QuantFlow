
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
                        <button onclick="openJsonModal('${token}')" class="px-3 py-1 text-[10px] font-bold uppercase tracking-wider rounded bg-indigo-600/20 text-indigo-300 hover:bg-indigo-500/40 hover:text-white transition-colors border border-indigo-500/30 shadow">🤖 Analyze</button>
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
        function openJsonModal(tokenOrSymbol) {
            if (!window.latestGlobalState) return;
            let token = tokenOrSymbol;
            if (!window.latestGlobalState[token]) {
                const searchStr = tokenOrSymbol.split('|').pop();
                const foundKey = Object.keys(window.latestGlobalState).find(k => 
                    (window.latestGlobalState[k].symbol || "").split('|').pop() === searchStr
                );
                if (foundKey) {
                    token = foundKey;
                } else {
                    return;
                }
            }
            const payload = window.latestGlobalState[token];
            const symbol = payload.symbol || token.split('|').pop();
            
            // Store globally for the clipboard function
            window.currentViewingToken = token;
            
            // Deep copy the structured payload if it exists, else fallback to raw
            let payloadCopy = {};
            if (payload.structured_payload) {
                payloadCopy = JSON.parse(JSON.stringify(payload.structured_payload));
            } else {
                payloadCopy = JSON.parse(JSON.stringify(payload));
            }
            
            // Append User Position if it exists in memory
            try {
                const allPos = JSON.parse(localStorage.getItem('user_positions')) || {};
                if (allPos[token]) {
                    if (payloadCopy.user_context) {
                        payloadCopy.user_context.position = allPos[token];
                    } else {
                        payloadCopy.user_position = allPos[token];
                    }
                }
            } catch (e) {
                console.error("Failed to append user position:", e);
            }
            
            document.getElementById('modal-title').innerHTML = `<span class="text-xl">🤖</span> AI Reasoning: ${symbol}`;
            document.getElementById('modal-code-block').innerText = JSON.stringify(payloadCopy, null, 2);
                        switchModalTab('reasoning');
            const catalystBlock = document.getElementById('catalyst-report-block');
            if (payloadCopy.latest_catalyst && payloadCopy.latest_catalyst.raw_news && payloadCopy.latest_catalyst.raw_news.length > 0) {
                let catText = `Current Cached Raw Catalyst:\n`;
                payloadCopy.latest_catalyst.raw_news.forEach(n => {
                    catText += `\n[${n.Article}] ${n.headline}\n${n.summary}\n`;
                });
                catalystBlock.innerText = catText;
            } else {
                catalystBlock.innerText = "No cached news catalyst available for this asset. Click 'Force Fetch News' to query live API.";
            }
            document.getElementById('llm-modal').classList.remove('hidden');
            // Phase 8 Reset UI state & fetch existing report
            renderReasoningReport("Awaiting trigger...");
            document.getElementById('toggle-auto-analyze').checked = false;
            if (window.autoAnalyzeIntervalId) { 
                clearInterval(window.autoAnalyzeIntervalId); 
                window.autoAnalyzeIntervalId = null; 
            }
            
            fetch(`${apiBase}/api/reasoning/report/${symbol}`)
                .then(r => r.json())
                .then(data => {
                    if (data.report) renderReasoningReport(data.report);
                    document.getElementById('toggle-auto-analyze').checked = data.is_active;
                    if (data.is_active) {
                        window.autoAnalyzeIntervalId = setInterval(async () => {
                            const res = await fetch(`${apiBase}/api/reasoning/report/${symbol}`);
                            const d = await res.json();
                            if (d.report) renderReasoningReport(d.report);
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
                
                if (data.status === 'success' && data.data) {
                    const newsData = data.data;
                    // CACHE IT LOCALLY SO IT DOESN'T VANISH ON REOPEN
                    if (window.latestGlobalState && window.latestGlobalState[token]) {
                        if (!window.latestGlobalState[token].structured_payload) {
                            window.latestGlobalState[token].structured_payload = {};
                        }
                        window.latestGlobalState[token].latest_catalyst = newsData;
                        window.latestGlobalState[token].structured_payload.latest_catalyst = newsData;
                    }
                    
                    // Prettify output
                    let out = `--- LATEST RAW CATALYST ---\n`;
                    if (newsData.raw_news && newsData.raw_news.length > 0) {
                        newsData.raw_news.forEach(n => {
                            out += `\n[${n.Article}] ${n.headline}\n${n.summary}\n`;
                        });
                    } else {
                        out += `\nNo recent news articles found for ${symbol}.\n`;
                    }
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
            const finalClipboardText = `${statusText}${intentText}SYSTEM INSTRUCTION:\n${currentPrompt}\n\nDATA PAYLOAD:\n${codeBlock}`;
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
                if (data.report) {
                    renderReasoningReport(data.report);
                } else {
                    reportBlock.innerText = "Error: Unexpected response -> " + JSON.stringify(data);
                }
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
                            renderReasoningReport(data.report);
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
    

        const DEFAULT_SYSTEM_PROMPT = `ROLE & OPERATIONAL FRAMEWORK:
You are the Lead Quantitative Execution Strategist. You are an API endpoint that ingests a Phase 6 Hierarchical JSON Telemetry Payload and outputs raw, deterministic execution logic. Your goal is to synthesize 4 data blocks to isolate true institutional positioning from retail traps, optimizing for a 2-6 hour intraday predictive horizon.

CRITICAL CONSTRAINTS & NULL SAFETY:
1. ABSOLUTE DATA ADHERENCE: Never assume or extrapolate targets. If metrics are null, 0, or state 'MARKET_CLOSED_SUPPRESSED', default to a neutral risk mitigation posture.
2. MARKET STATE SHIELD: If root level \`market_state\` is 'CLOSED' or 'AUCTION', immediately output Action: 'Wait' or 'Hold' with a Priority_Score of 0. Do not calculate new trade targets on post-market ghost books.

STEP 1: THE 4-BLOCK MULTI-VARIABLE SYNTHESIS MATRIX
You must cross-examine the payload fields using the following strict institutional logic:

1. BLOCK 1: LIVE MICROSTRUCTURE & INTENT ANALYSIS
   - Evaluate \`price_to_vwap_pct\` against \`kinetic_divergence.divergence_state\`. If state is 'HIDDEN_BULLISH_ABSORPTION', price drops are artificial liquidity sweeps; you must heavily favor LONG or HOLD positions. If 'HIDDEN_BEARISH_DISTRIBUTION', favor SHORT or CLOSE.
   - Evaluate \`mtf_technicals.elasticity_risk\`. If it reads 'OVERSTRETCHED', you face an imminent mean-reversion snapback. You MUST penalize breakout continuation trades. Only authorize mean-reversion setups or 'Wait'.
   - Read \`mtf_technicals.key_geometry\`. If LTP is within 0.2% of a reversal neckline (e.g., \`double_top\`) on high volume (\`vol_z_score_5m\` > 2), anticipate a structural breakout or immediate rejection.

2. BLOCK 2: DERIVATIVES MATRIX (THE PRICING REALITY)
   - Analyze \`volatility_edge.ivr_live\` and \`iv_percentile_52w\`. High levels (>70) indicate massive premium expansion. Require an overwhelming structural edge to buy into expansion.
   - Synthesize \`options_positioning.max_pain_divergence_pct\`. If divergence is > 5% and expiration is approaching, apply a structural gravity factor dragging LTP toward \`max_pain_price\`.

3. BLOCK 3: MACRO STATISTICAL EDGE (THE CONCRETE WALLS)
   - Measure LTP against \`structural_liquidity.volume_poc_price\`. This is an absolute multi-year liquidity wall. Never short directly on top of a 5-year POC floor, and never long directly under a major Value Area High rejection.
   - Cross-check \`regime_confluence.alpha_vs_nifty_5y\`. If alpha is highly negative, the stock has persistent secular weakness. Short setups require less volume conviction than long setups.

4. BLOCK 4: CATALYST ENGINE
   - Parse the \`raw_news\` array. Map news sentiment directly against Block 1 order flow. If headlines are highly bullish but \`whale_cvd_ema_1h\` is flat/negative, classify the asset as an active Institutional Distribution Trap and avoid long entries.

STEP 2: DIRECTIONAL CONTEXT & TRADE ACTIONS
- If \`user_context.position\` is completely empty: You are hunting entries. Output Action as 'Long', 'Short', or 'Wait'.
- If \`user_context.position\` exists: You are managing risk. You are restricted to outputting 'Hold', 'Close', or 'Wait'. Evaluate position PnL using entry price vs LTP and match against local structural stops.

OUTPUT FORMAT:
Output NOTHING except a raw, valid JSON object that can be directly passed to \`json.loads()\`. Do not wrap the output in markdown blocks, backticks, or prepend text. Every numeric field must be a float or null, strings must be exact matches.

{
  "Action": "Short/Long/Hold/Close/Wait",
  "Entry_Target_Price": <float or null>,
  "Stoploss": <float or null>,
  "Exit_Target_Price": <float or null>,
  "Confidence_Score": <int from 1 to 10>,
  "Risk_Percentage": <float>,
  "Priority_Score": <int from 1 to 10>, // Set > 6 ONLY if a high-conviction asymmetric edge or critical position exit exists right now
  "Reason": "<string>" // CRITICAL: Exactly 1-2 dense sentences detailing the precise multi-block convergence (e.g., Whale Absorption vs. Macro Walls) that dictates this action.
}`;
        
        // Force update if the old prompt is still present or if it doesn't contain the new strictly JSON string check
        const currentStoredPrompt = localStorage.getItem('llm_system_prompt');
        if (currentStoredPrompt && currentStoredPrompt.includes('===ADVICE_SPLIT===')) {
            localStorage.setItem('llm_system_prompt', DEFAULT_SYSTEM_PROMPT);
        }
        // Initialization
        if (!localStorage.getItem('llm_system_prompt')) {
            localStorage.setItem('llm_system_prompt', DEFAULT_SYSTEM_PROMPT);
        }
        const btnSettings = document.getElementById('btn-settings');
        const settingsModal = document.getElementById('settings-modal');
        const btnCloseSettings = document.getElementById('btn-close-settings');
        const promptEditor = document.getElementById('prompt-editor');
        const btnSaveSettings = document.getElementById('btn-save-settings');
        const btnResetSettings = document.getElementById('btn-reset-settings');
        // Open Modal
        btnSettings.addEventListener('click', () => {
            promptEditor.value = localStorage.getItem('llm_system_prompt') || DEFAULT_SYSTEM_PROMPT;
            settingsModal.classList.remove('hidden');
        });
        // Close Modal
        btnCloseSettings.addEventListener('click', () => {
            settingsModal.classList.add('hidden');
        });
        // Save Configuration
        btnSaveSettings.addEventListener('click', () => {
            const newValue = promptEditor.value.trim();
            if (newValue) {
                localStorage.setItem('llm_system_prompt', newValue);
                alert("Prompt Configuration Saved Successfully!");
                settingsModal.classList.add('hidden');
            } else {
                alert("Prompt cannot be empty!");
            }
        });
        // Reset Configuration
        btnResetSettings.addEventListener('click', () => {
            if(confirm("Are you sure you want to reset the prompt to its factory default?")) {
                promptEditor.value = DEFAULT_SYSTEM_PROMPT;
                localStorage.setItem('llm_system_prompt', DEFAULT_SYSTEM_PROMPT);
                alert("Prompt Reset to Default!");
            }
        });
        // --- Position Manager Logic ---
        if (!localStorage.getItem('user_positions')) {
            localStorage.setItem('user_positions', JSON.stringify({}));
        }
        const posModal = document.getElementById('position-modal');
        const btnClosePos = document.getElementById('btn-close-pos');
        const posTitle = document.getElementById('pos-modal-title');
        
        function openPositionModal(tokenOrSymbol) {
            let token = tokenOrSymbol;
            if (window.latestGlobalState && !window.latestGlobalState[token]) {
                const searchStr = tokenOrSymbol.split('|').pop();
                const foundKey = Object.keys(window.latestGlobalState).find(k => 
                    (window.latestGlobalState[k].symbol || "").split('|').pop() === searchStr
                );
                if (foundKey) token = foundKey;
            }
            const state = window.latestGlobalState && window.latestGlobalState[token] ? window.latestGlobalState[token] : null;
            const symbol = state ? state.symbol : `Token ${token}`;
            
            posTitle.innerHTML = `📝 Edit Position: ${symbol}`;
            document.getElementById('pos-token').value = token;
            
            // Extract AI recommended data if available
            let defaultDirection = 'Long';
            let defaultPrice = '';
            
            const liveReport = window.latestLiveReports ? window.latestLiveReports[symbol] : null;
            const reportToParse = liveReport || (state ? state.latest_report : null);
            
            if (reportToParse) {
                const jsonMatch = reportToParse.match(/```(?:json)?\s*(\{[\s\S]*?\})\s*```/);
                if (jsonMatch) {
                    try {
                        const actionData = JSON.parse(jsonMatch[1]);
                        if (actionData.Entry_Target_Price) defaultPrice = actionData.Entry_Target_Price;
                        if (actionData.Action) {
                            const act = actionData.Action.toLowerCase();
                            if (act.includes('short') || act.includes('sell')) defaultDirection = 'Short';
                            else if (act.includes('long') || act.includes('buy')) defaultDirection = 'Long';
                        }
                    } catch(e) {}
                }
            }
            // Load existing data if available
            const allPosStr = localStorage.getItem('user_positions');
            const allPos = allPosStr ? JSON.parse(allPosStr) : {};
            const myPos = allPos[token];
            
            if (myPos) {
                document.getElementById('pos-mode').value = myPos.mode || 'Intraday';
                document.getElementById('pos-direction').value = myPos.direction || defaultDirection;
                document.getElementById('pos-qty').value = myPos.quantity || '';
                document.getElementById('pos-price').value = myPos.entry_price || defaultPrice;
                document.getElementById('pos-target').value = myPos.target || '';
                document.getElementById('pos-stoploss').value = myPos.stoploss || '';
            } else {
                document.getElementById('pos-mode').value = 'Intraday';
                document.getElementById('pos-direction').value = defaultDirection;
                document.getElementById('pos-qty').value = '';
                document.getElementById('pos-price').value = defaultPrice;
                document.getElementById('pos-target').value = '';
                document.getElementById('pos-stoploss').value = '';
            }
            
            posModal.classList.remove('hidden');
        }
        
        btnClosePos.addEventListener('click', () => {
            posModal.classList.add('hidden');
        });
        
        document.getElementById('btn-save-pos').addEventListener('click', () => {
            const token = document.getElementById('pos-token').value;
            const qty = parseFloat(document.getElementById('pos-qty').value);
            const price = parseFloat(document.getElementById('pos-price').value);
            const targetVal = parseFloat(document.getElementById('pos-target').value);
            const stoplossVal = parseFloat(document.getElementById('pos-stoploss').value);
            if (isNaN(qty) || isNaN(price) || qty <= 0 || price <= 0) {
                alert("Please enter valid positive numbers for Quantity and Price.");
                return;
            }
            
            const allPosStr = localStorage.getItem('user_positions');
            const allPos = allPosStr ? JSON.parse(allPosStr) : {};
            const existingPos = allPos[token];
            const posData = {
                mode: document.getElementById('pos-mode').value,
                direction: document.getElementById('pos-direction').value,
                quantity: qty,
                entry_price: price,
                target: isNaN(targetVal) ? null : targetVal,
                stoploss: isNaN(stoplossVal) ? null : stoplossVal,
                entry_timestamp: existingPos && existingPos.entry_timestamp ? existingPos.entry_timestamp : Math.floor(Date.now() / 1000)
            };
            allPos[token] = posData;
            localStorage.setItem('user_positions', JSON.stringify(allPos));
            posModal.classList.add('hidden');
            console.log(`Position for Token ${token} saved.`);
            // Sync with backend positions registry
            fetch(`${apiBase}/api/reasoning/position/save`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol: token, user_position: posData })
            });
            // Hot-reload the backend loop if global auto-analyze is running
            if (document.getElementById('global-auto-analyze').checked) {
                const freq = document.getElementById('global-analyze-interval').value;
                fetch(`${apiBase}/api/reasoning/loop/start`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ symbol: token, interval: parseInt(freq), user_position: posData })
                });
            }
        });
        document.getElementById('btn-clear-pos').addEventListener('click', () => {
            const token = document.getElementById('pos-token').value;
            const allPos = JSON.parse(localStorage.getItem('user_positions')) || {};
            if (allPos[token]) {
                delete allPos[token];
                localStorage.setItem('user_positions', JSON.stringify(allPos));
            }
            posModal.classList.add('hidden');
            console.log(`Position for Token ${token} cleared.`);
            // Sync with backend positions registry (clear)
            fetch(`${apiBase}/api/reasoning/position/save`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol: token, user_position: null })
            });
            // Hot-reload the backend loop if global auto-analyze is running
            if (document.getElementById('global-auto-analyze').checked) {
                const freq = document.getElementById('global-analyze-interval').value;
                fetch(`${apiBase}/api/reasoning/loop/start`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ symbol: token, interval: parseInt(freq), user_position: null })
                });
            }
        });
        // Phase 5.5: Map Option Tokens Logic
        const btnMapTokens = document.getElementById('btn-map-tokens');
        if (btnMapTokens) {
            btnMapTokens.addEventListener('click', () => {
                const originalText = btnMapTokens.innerText;
                btnMapTokens.innerText = "Mapping Tokens (Please Wait...)";
                btnMapTokens.disabled = true;
                btnMapTokens.classList.add('opacity-50', 'cursor-not-allowed');
                fetch(`${apiBase}/api/map-option-tokens`, { method: 'POST' })
                    .then(response => response.json())
                    .then(data => {
                        if (data.status === 'success') {
                            // Flash brief success notification
                            const alertBox = document.createElement('div');
                            alertBox.className = 'fixed top-4 right-4 bg-purple-600 text-white px-4 py-2 rounded shadow-lg z-50 font-bold text-sm transition-opacity duration-500';
                            alertBox.innerText = "Live Option Tokens Injected into Data Stream.";
                            document.body.appendChild(alertBox);
                            setTimeout(() => {
                                alertBox.style.opacity = '0';
                                setTimeout(() => alertBox.remove(), 500);
                            }, 3000);
                        } else {
                            alert("Mapping failed: " + data.message);
                        }
                    })
                    .catch(error => {
                        console.error('Error mapping tokens:', error);
                        alert('An error occurred while mapping option tokens.');
                    })
                    .finally(() => {
                        btnMapTokens.innerText = originalText;
                        btnMapTokens.disabled = false;
                        btnMapTokens.classList.remove('opacity-50', 'cursor-not-allowed');
                    });
            });
        }
        
        // --- Phase 10: Watchlist Manager Logic ---
        const watchlistModal = document.getElementById('watchlist-modal');
        let currentWatchlist = [];
        async function openWatchlistModal() {
            document.getElementById('watchlist-modal').classList.remove('hidden');
            try {
                const res = await fetch(`${apiBase}/api/watchlist`);
                const data = await res.json();
                currentWatchlist = data.data || [];
                renderWatchlist();
            } catch (e) {
                console.error("Failed to fetch watchlist", e);
            }
        }
        function closeWatchlistModal() {
            document.getElementById('watchlist-modal').classList.add('hidden');
        }
        function renderWatchlist() {
            const list = document.getElementById('watchlist-items');
            list.innerHTML = '';
            currentWatchlist.forEach((item, idx) => {
                const li = document.createElement('li');
                li.className = "flex justify-between items-center bg-slate-800 p-2 rounded text-sm mb-1 border border-slate-700";
                li.innerHTML = `<span><span class="font-bold text-slate-200">${item.symbol}</span> <span class="text-xs text-slate-500 ml-1">(${item.exchange}:${item.token})</span></span>
                                <button onclick="removeWatchlistItem(${idx})" class="text-rose-400 hover:text-rose-300">🗑️</button>`;
                list.appendChild(li);
            });
        }
        function removeWatchlistItem(idx) {
            currentWatchlist.splice(idx, 1);
            renderWatchlist();
        }
        let activeWatchlistStream = null;
        function switchTab(tabId) {
            try {
                const tabDashboard = document.getElementById('tab-dashboard');
                const tabDiscovery = document.getElementById('tab-screener');
                const tabLiveAction = document.getElementById('tab-live-action');
                const btnDashboard = document.getElementById('tab-btn-dashboard');
                const btnDiscovery = document.getElementById('tab-btn-screener');
                const btnLiveAction = document.getElementById('tab-btn-live-action');
                if (tabDashboard) {
                    tabDashboard.classList.add('hidden');
                    tabDashboard.classList.remove('block');
                }
                if (tabDiscovery) {
                    tabDiscovery.classList.add('hidden');
                    tabDiscovery.classList.remove('block');
                }
                if (tabLiveAction) {
                    tabLiveAction.classList.add('hidden');
                    tabLiveAction.classList.remove('block');
                }
                if (btnDashboard) {
                    btnDashboard.className = "px-4 py-1.5 text-xs font-bold rounded-md text-slate-400 hover:text-slate-200 transition-all border border-transparent";
                }
                if (btnDiscovery) {
                    btnDiscovery.className = "px-4 py-1.5 text-xs font-bold rounded-md text-slate-400 hover:text-slate-200 transition-all border border-transparent";
                }
                if (btnLiveAction) {
                    btnLiveAction.className = "px-4 py-1.5 text-xs font-bold rounded-md text-slate-400 hover:text-slate-200 transition-all border border-transparent";
                }
                
                const targetTab = document.getElementById(`tab-${tabId}`);
                if (targetTab) {
                    targetTab.classList.remove('hidden');
                    targetTab.classList.add('block');
                }
                
                const targetBtn = document.getElementById(`tab-btn-${tabId}`);
                if (targetBtn) {
                    targetBtn.className = "px-4 py-1.5 text-xs font-bold rounded-md bg-slate-800 text-white shadow transition-all border border-slate-700/50";
                }
                // Polling control
                if (tabId === 'live-action') {
                    if (typeof startLiveActionPolling === 'function') startLiveActionPolling();
                } else {
                    if (typeof stopLiveActionPolling === 'function') stopLiveActionPolling();
                }
            } catch (err) {
                console.error("Error in switchTab: ", err);
            }
        }
        let lastPlaybookStr = '';
        function renderDiscoveryCards(watchlist) {
              try {
                  const grid = document.getElementById('screener-cards');
                  if (!grid) return;
                  grid.innerHTML = '';
                  
                  if (!watchlist || !Array.isArray(watchlist)) {
                      grid.innerHTML = `<div class="col-span-full p-4 bg-amber-900/50 text-amber-300 rounded border border-amber-700">Invalid watchlist data</div>`;
                      return;
                  }
                  
                  if (watchlist.length === 0) {
                     grid.innerHTML = `<div class="col-span-full p-4 bg-amber-900/50 text-amber-300 rounded border border-amber-700">Watchlist is an empty array []</div>`;
                     return;
                }
                
                watchlist.forEach((item, idx) => {
                    try {
                        const card = document.createElement('div');
                        // ADDED display:flex and explicit min-height to FORCE it to show up even if Tailwind is broken
                        card.style.minHeight = "200px";
                        card.style.display = "flex";
                        card.className = "bg-slate-900 border border-slate-700/80 rounded-xl p-5 shadow-lg flex-col justify-between hover:border-indigo-500/50 transition-colors group relative overflow-hidden";
                        
                        card.innerHTML = `
                            <div class="absolute top-0 right-0 w-24 h-24 bg-indigo-500/10 rounded-bl-full -mr-4 -mt-4 transition-transform group-hover:scale-110"></div>
                            <div class="relative z-10">
                                <div class="flex justify-between items-start mb-3">
                                    <h3 class="text-xl font-bold text-white tracking-tight">${item.symbol || 'N/A'}</h3>
                                    <span class="px-2 py-0.5 text-[10px] font-bold rounded bg-indigo-900/40 text-indigo-300 border border-indigo-700/50 uppercase">Top Pick</span>
                                </div>
                                <p class="text-xs text-slate-300 mb-3 leading-relaxed"><strong>Rationale:</strong> ${item.rationale || 'N/A'}</p>
                                <p class="text-xs text-emerald-400 mb-3 bg-emerald-950/30 p-2 rounded border border-emerald-900/50"><strong>Strategy:</strong> ${item.strategy || 'N/A'}</p>
                                
                                <div class="grid grid-cols-3 gap-2 mb-4 mt-2">
                                    <div class="bg-slate-800/50 p-2 rounded border border-slate-700/50 text-center">
                                        <div class="text-[10px] text-slate-400 uppercase tracking-wider mb-1">Entry</div>
                                        <div class="text-xs font-bold text-indigo-400">${item.entry || 'N/A'}</div>
                                    </div>
                                    <div class="bg-slate-800/50 p-2 rounded border border-slate-700/50 text-center">
                                        <div class="text-[10px] text-slate-400 uppercase tracking-wider mb-1">Target</div>
                                        <div class="text-xs font-bold text-emerald-400">${item.target || 'N/A'}</div>
                                    </div>
                                    <div class="bg-slate-800/50 p-2 rounded border border-slate-700/50 text-center">
                                        <div class="text-[10px] text-slate-400 uppercase tracking-wider mb-1">Stop</div>
                                        <div class="text-xs font-bold text-rose-400">${item.stoploss || 'N/A'}</div>
                                    </div>
                                </div>
                                
                                <div class="flex justify-between items-center mb-4 px-1">
                                    <div class="flex items-center gap-1.5">
                                        <span class="text-[10px] text-slate-500 uppercase tracking-wider">Confidence:</span>
                                        <span class="text-xs font-bold ${item.confidence && item.confidence.toLowerCase() === 'high' ? 'text-emerald-400' : 'text-amber-400'}">${item.confidence || 'N/A'}</span>
                                    </div>
                                    <div class="flex items-center gap-1.5">
                                        <span class="text-[10px] text-slate-500 uppercase tracking-wider">Risk:</span>
                                        <span class="text-xs font-bold ${item.risk && item.risk.toLowerCase() === 'high' ? 'text-rose-400' : 'text-amber-400'}">${item.risk || 'N/A'}</span>
                                    </div>
                                </div>
                            </div>
                            <button onclick="addToWatchlistDynamic('${item.token || ''}', '${item.symbol || ''}', '${item.exchange || ''}')" class="mt-auto w-full bg-slate-800 hover:bg-indigo-600 text-white text-xs font-bold py-2 rounded transition-colors border border-slate-700 hover:border-indigo-500 flex justify-center items-center gap-2 z-10 relative">
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"></path></svg>
                                Add to Live Watchlist
                            </button>
                        `;
                        grid.appendChild(card);
                    } catch (err) {
                        const errDiv = document.createElement('div');
                        errDiv.className = "text-rose-400 text-xs";
                        errDiv.innerText = "Error rendering " + (item.symbol || 'card') + ": " + err.message;
                        grid.appendChild(errDiv);
                    }
                });
            } catch (err) {
                const grid = document.getElementById('screener-cards');
                if (grid) grid.innerHTML = `<div class="col-span-full text-rose-500 font-bold">FATAL ERROR in renderDiscoveryCards: ${err.message}</div>`;
            }
        }
        async function addToWatchlistDynamic(token, symbol, exchange) {
            try {
                const res = await fetch(`${apiBase}/api/watchlist/add`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ token, symbol, exchange })
                });
                const data = await res.json();
                if(res.ok) {
                    alert(`Successfully added ${symbol} to Live Watchlist! The websocket stream will hot-load it.`);
                } else {
                    alert("Error: " + data.message);
                }
            } catch(e) {
                console.error(e);
                alert("Failed to add to watchlist.");
            }
        }
        async function saveWatchlist() {
            const btn = document.getElementById('btn-save-watchlist');
            btn.innerText = "Saving...";
            try {
                const res = await fetch(`${apiBase}/api/watchlist`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ items: currentWatchlist })
                });
                if(res.ok) {
                    alert("Watchlist updated successfully! Data stream reinitialized.");
                    closeWatchlistModal();
                } else {
                    alert("Failed to save watchlist.");
                }
            } catch (e) {
                alert("Error saving watchlist.");
            } finally {
                btn.innerText = "Save Changes";
            }
        }
        let searchTimeout;
        async function onWatchlistSearch() {
            const query = document.getElementById('watchlist-search').value;
            const resDiv = document.getElementById('watchlist-search-results');
            if (query.length < 2) { resDiv.innerHTML = ''; return; }
            
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(async () => {
                try {
                    const res = await fetch(`${apiBase}/api/search-token?q=${query}`);
                    const data = await res.json();
                    resDiv.innerHTML = '';
                    (data.data || []).forEach(item => {
                        const div = document.createElement('div');
                        div.className = "p-2 hover:bg-slate-700 cursor-pointer text-sm flex justify-between border-b border-slate-700";
                        div.innerHTML = `<span><span class="font-bold text-slate-200">${item.symbol}</span></span> <span class="text-xs text-slate-400">${item.exchange}:${item.token}</span>`;
                        div.onclick = () => {
                            if(!currentWatchlist.find(x => x.token === item.token)) {
                                currentWatchlist.push(item);
                                renderWatchlist();
                            }
                            resDiv.innerHTML = '';
                            document.getElementById('watchlist-search').value = '';
                        };
                        resDiv.appendChild(div);
                    });
                } catch(e) {
                    console.error("Search failed", e);
                }
            }, 300);
        }
        // --- News Engine Controls ---
        let newsStateInterval = null;
        let newsRequestInFlight = false;
        function fetchNewsState() {
            if (newsRequestInFlight) return;
            fetch(`${apiBase}/api/news/state`)
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        document.getElementById('toggle-news-auto').checked = data.is_active;
                        if (document.activeElement !== document.getElementById('news-interval')) {
                            document.getElementById('news-interval').value = data.interval;
                        }
                        if (document.activeElement !== document.getElementById('news-model-select')) {
                            document.getElementById('news-model-select').value = data.model;
                        }
                        
                        const statusElem = document.getElementById('news-status-text');
                        if (data.is_active) {
                            const lastFetch = data.last_fetch_time > 0 ? new Date(data.last_fetch_time * 1000).toLocaleTimeString() : 'Never';
                            statusElem.innerHTML = `<span class="text-emerald-400">Running (Last: ${lastFetch})</span>`;
                        } else {
                            statusElem.innerHTML = `<span class="text-slate-500">Stopped</span>`;
                        }
                    }
                })
                .catch(err => console.error("Error fetching news state:", err));
        }
        function triggerNewsInstant() {
            const model = document.getElementById('news-model-select').value;
            const btn = document.getElementById('btn-news-instant');
            const origHtml = btn.innerHTML;
            btn.innerHTML = '⏳ Fetching...';
            btn.disabled = true;
            fetch(`${apiBase}/api/news/instant`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model: model })
            })
            .then(r => r.json())
            .then(data => {
                fetchNewsState();
                setTimeout(() => { btn.innerHTML = origHtml; btn.disabled = false; }, 1000);
            })
            .catch(err => {
                console.error(err);
                btn.innerHTML = origHtml;
                btn.disabled = false;
            });
        }
        function toggleNewsAuto() {
            const isChecked = document.getElementById('toggle-news-auto').checked;
            newsRequestInFlight = true;
            if (isChecked) {
                const model = document.getElementById('news-model-select').value;
                const interval = parseInt(document.getElementById('news-interval').value) || 120;
                fetch(`${apiBase}/api/news/loop/start`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ interval: interval, model: model })
                }).then(() => {
                    newsRequestInFlight = false;
                    fetchNewsState();
                }).catch(() => newsRequestInFlight = false);
            } else {
                fetch(`${apiBase}/api/news/loop/stop`, { method: 'POST' }).then(() => {
                    newsRequestInFlight = false;
                    fetchNewsState();
                }).catch(() => newsRequestInFlight = false);
            }
        }
        async function generateIntradayPlaybook() {
            const btn = document.getElementById('btn-run-discovery');
            const status = document.getElementById('screener-status');
            const empty = document.getElementById('screener-empty');
            const grid = document.getElementById('screener-cards');
            if (btn) { btn.disabled = true; btn.classList.add('opacity-50'); }
            if (empty) empty.classList.add('hidden');
            if (grid) grid.innerHTML = '';
            if (status) {
                status.classList.remove('hidden');
                status.classList.add('block');
            }
            try {
                const modelSelect = document.getElementById('news-model-select');
                const model = modelSelect ? modelSelect.value : 'gemini-2.5-flash';
                const res = await fetch(`${apiBase}/api/reasoning/playbook/generate`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({model: model})
                });
                if (res.ok) {
                    showToast("Playbook Generation Triggered", "The AI is compiling the multi-factor matrix in the background. Please wait a few seconds...");
                }
            } catch (e) {
                console.error(e);
                showToast("Error", "Failed to contact API server.", "error");
                if (status) { status.classList.add('hidden'); status.classList.remove('block'); }
                if (btn) { btn.disabled = false; btn.classList.remove('opacity-50'); }
            }
        }
        // --- Database Management Controls (Phase 14.3) ---
        function loadParquetSymbols() {
            fetch(`${apiBase}/api/watchlist`)
                .then(r => r.json())
                .then(data => {
                    const grid = document.getElementById('parquet-symbols-grid');
                    if (!grid) return;
                    const items = data.data || [];
                    if (items.length === 0) {
                        grid.innerHTML = '<span class="text-xs text-slate-500 italic">No symbols in watchlist.</span>';
                        return;
                    }
                    grid.innerHTML = '';
                    const uniqueSymbols = [...new Set(items.map(i => i.symbol.split('-')[0]))];
                    uniqueSymbols.forEach(sym => {
                        if (!sym || sym.toLowerCase() === 'nifty 50') return; // Skip invalid
                        const label = document.createElement('label');
                        label.className = "flex items-center gap-2 cursor-pointer p-1 hover:bg-slate-800 rounded transition-colors";
                        label.innerHTML = `
                            <input type="checkbox" value="${sym}" class="parquet-sym-checkbox w-3 h-3 bg-slate-900 border-slate-700 rounded text-orange-600 focus:ring-orange-500">
                            <span class="text-xs text-slate-300 font-mono-custom">${sym}</span>
                        `;
                        grid.appendChild(label);
                    });
                })
                .catch(err => console.error("Failed to load symbols for parquet:", err));
        }
        function selectAllParquetSymbols() {
            const checkboxes = document.querySelectorAll('.parquet-sym-checkbox');
            const allChecked = Array.from(checkboxes).every(cb => cb.checked);
            checkboxes.forEach(cb => cb.checked = !allChecked);
        }
        function syncEODParquet() {
            const checkboxes = document.querySelectorAll('.parquet-sym-checkbox:checked');
            const symbols = Array.from(checkboxes).map(cb => cb.value);
            
            const btn = document.getElementById('btn-sync-parquet');
            const status = document.getElementById('parquet-sync-status');
            
            if (symbols.length === 0) {
                status.innerHTML = `<span class="text-rose-400">Select at least one symbol.</span>`;
                status.classList.remove('hidden');
                setTimeout(() => status.classList.add('hidden'), 3000);
                return;
            }
            btn.disabled = true;
            btn.innerHTML = '⏳ Syncing...';
            status.innerHTML = `<span class="text-orange-400">Syncing Database...</span>`;
            status.classList.remove('hidden');
            fetch(`${apiBase}/api/admin/sync-parquet`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbols: symbols })
            })
            .then(r => r.json())
            .then(data => {
                btn.innerHTML = '💾 Force Sync EOD Parquet';
                btn.disabled = false;
                status.innerHTML = `<span class="text-emerald-400">Sync Complete. Check terminal logs.</span>`;
                setTimeout(() => status.classList.add('hidden'), 5000);
            })
            .catch(err => {
                console.error(err);
                btn.innerHTML = '💾 Force Sync EOD Parquet';
                btn.disabled = false;
                status.innerHTML = `<span class="text-rose-400">Sync Failed.</span>`;
            });
        }
        // Initialize News Engine
        document.addEventListener('DOMContentLoaded', () => {
            fetchNewsState();
            loadParquetSymbols();
            newsStateInterval = setInterval(fetchNewsState, 5000);
        });
        function renderReasoningReport(reportText) {
            const el = document.getElementById('reasoning-report-block');
            if (!el) return;
            
            let data = null;
            let cleanText = reportText.trim();
            if (cleanText.startsWith('```json')) cleanText = cleanText.substring(7);
            else if (cleanText.startsWith('```')) cleanText = cleanText.substring(3);
            if (cleanText.endsWith('```')) cleanText = cleanText.substring(0, cleanText.length - 3);
            cleanText = cleanText.trim();

            try {
                data = JSON.parse(cleanText);
            } catch (e) {
                // If it fails to parse, just render as text
                el.innerText = reportText;
                return;
            }
            
            let html = ``;
            if (data.Reason) {
                html += `<div class="mb-4 text-slate-300 font-mono-custom whitespace-pre-wrap">${data.Reason}</div>`;
            }
                    
                    const actionClass = data.Action === 'Short' ? 'bg-rose-900/40 text-rose-300 border-rose-700/50' : data.Action === 'Wait' || data.Action === 'Hold' ? 'bg-amber-900/40 text-amber-300 border-amber-700/50' : 'bg-emerald-900/40 text-emerald-300 border-emerald-700/50';
                    // Build the beautiful UI card
                    let actionHtml = `
                    <div class="bg-slate-900 border border-slate-700/80 rounded-xl p-4 shadow-lg relative overflow-hidden flex flex-col shrink-0">
                        <div class="absolute top-0 right-0 w-24 h-24 bg-indigo-500/10 rounded-bl-full -mr-4 -mt-4"></div>
                        <div class="relative z-10">
                            <div class="flex justify-between items-start mb-3">
                                <h3 class="text-lg font-bold text-white tracking-tight">Action Plan</h3>
                                <span class="px-2 py-0.5 text-xs font-bold rounded ${actionClass} border uppercase">${data.Action || 'N/A'}</span>
                            </div>
                            <div class="grid grid-cols-2 gap-2 mb-4 mt-2">
                                <div class="bg-slate-800/50 p-2 rounded border border-slate-700/50 text-center">
                                    <div class="text-xs text-slate-400 uppercase tracking-wider mb-1">Entry</div>
                                    <div class="text-sm font-bold text-indigo-400">${data.Entry_Target_Price || 'N/A'}</div>
                                </div>
                                <div class="bg-slate-800/50 p-2 rounded border border-slate-700/50 text-center">
                                    <div class="text-xs text-slate-400 uppercase tracking-wider mb-1">Target</div>
                                    <div class="text-sm font-bold text-emerald-400">${data.Exit_Target_Price || 'N/A'}</div>
                                </div>
                                <div class="bg-slate-800/50 p-2 rounded border border-slate-700/50 text-center col-span-2">
                                    <div class="text-xs text-slate-400 uppercase tracking-wider mb-1">Stop Loss</div>
                                    <div class="text-sm font-bold text-rose-400">${data.Stoploss || 'N/A'}</div>
                                </div>
                            </div>
                            <div class="flex justify-between items-center px-1 mt-2">
                                <div class="flex items-center gap-1.5">
                                    <span class="text-xs text-slate-500 uppercase tracking-wider">Confidence:</span>
                                    <span class="text-sm font-bold text-indigo-400">${data.Confidence_Score ? data.Confidence_Score + '/10' : 'N/A'}</span>
                                </div>
                                <div class="flex items-center gap-1.5">
                                    <span class="text-xs text-slate-500 uppercase tracking-wider">Risk:</span>
                                    <span class="text-sm font-bold text-amber-400">${data.Risk_Percentage ? data.Risk_Percentage + '%' : 'N/A'}</span>
                                </div>
                            </div>
                        </div>
                    </div>`;
                    const actionBlock = document.getElementById('reasoning-action-block');
                    if (actionBlock) {
                        actionBlock.innerHTML = actionHtml;
                        actionBlock.classList.remove('hidden');
                        actionBlock.classList.add('flex');
                    }
            el.innerHTML = html;
        }
    

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
            window.latestLiveReports = data.reports;
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
        
        let cleanText = text.trim();
        if (cleanText.startsWith('```json')) cleanText = cleanText.substring(7);
        else if (cleanText.startsWith('```')) cleanText = cleanText.substring(3);
        if (cleanText.endsWith('```')) cleanText = cleanText.substring(0, cleanText.length - 3);
        cleanText = cleanText.trim();
        
        try {
            const actionData = JSON.parse(cleanText);
            cardsData.push({ symbol, text, actionData });
        } catch (e) {
            // Not valid JSON, ignore
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
                <div class="flex flex-wrap items-center gap-1.5">
                    <h3 class="text-xl font-bold text-white tracking-tight mr-1">${card.symbol.split('|').pop()}</h3>
                    <span class="px-2 py-0.5 text-xs font-bold rounded ${isHighPriority ? 'bg-emerald-900/60 text-emerald-400 border border-emerald-700' : 'bg-slate-800 text-slate-400 border border-slate-700'}">Pri: ${score}</span>
                    <span class="px-2 py-0.5 text-[10px] font-bold rounded bg-indigo-900/40 text-indigo-300 border border-indigo-700">Conf: ${d.Confidence_Score || '-'}</span>
                </div>
                <span class="px-2 py-0.5 text-xs font-bold rounded ${actionClass} border uppercase">${d.Action || 'N/A'}</span>
            </div>
            <div class="grid grid-cols-2 gap-2 mb-4 mt-2">
                <div class="bg-slate-800/50 p-1.5 rounded border border-slate-700/50 text-center">
                    <div class="text-[10px] text-slate-400 uppercase tracking-wider mb-0.5">Entry</div>
                    <div class="text-sm font-bold text-indigo-400">${d.Entry_Target_Price || 'N/A'}</div>
                </div>
                <div class="bg-slate-800/50 p-1.5 rounded border border-slate-700/50 text-center">
                    <div class="text-[10px] text-slate-400 uppercase tracking-wider mb-0.5">Target</div>
                    <div class="text-sm font-bold text-emerald-400">${d.Exit_Target_Price || 'N/A'}</div>
                </div>
                <div class="bg-slate-800/50 p-1.5 rounded border border-slate-700/50 text-center">
                    <div class="text-[10px] text-slate-400 uppercase tracking-wider mb-0.5">Stop Loss</div>
                    <div class="text-sm font-bold text-rose-400">${d.Stoploss || 'N/A'}</div>
                </div>
                <div class="bg-slate-800/50 p-1.5 rounded border border-slate-700/50 text-center">
                    <div class="text-[10px] text-slate-400 uppercase tracking-wider mb-0.5">Risk</div>
                    <div class="text-sm font-bold text-amber-400">${d.Risk_Percentage ? d.Risk_Percentage + '%' : 'N/A'}</div>
                </div>
            </div>
            <div class="flex justify-between items-center gap-2 mt-auto pt-2 border-t border-slate-800">
                <button onclick="forceReasoning('${card.symbol}')" class="flex-1 bg-indigo-600/20 hover:bg-indigo-600/40 text-indigo-300 border border-indigo-700/50 py-1.5 rounded text-xs font-bold transition-colors">Instant</button>
                <button onclick="openJsonModal('${card.symbol}')" class="w-8 h-8 flex items-center justify-center bg-slate-800 hover:bg-slate-700 text-slate-400 border border-slate-700 rounded transition-colors" title="Settings">⚙️</button>
                <button onclick="showLiveAnalysisModal('${escapedText}')" class="flex-1 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 py-1.5 rounded text-xs font-bold transition-colors">Analysis</button>
            </div>
            <div class="flex justify-between items-center gap-2 mt-2">
                <button onclick="openPositionModal('${card.symbol}')" class="flex-1 bg-slate-800 hover:bg-slate-700 text-slate-400 border border-slate-700 py-1.5 rounded text-[10px] uppercase font-bold transition-colors">Position</button>
                <button onclick="acceptLiveActionCard('${card.symbol}', '${escapedText}')" class="flex-1 bg-emerald-600/20 hover:bg-emerald-600/40 text-emerald-400 border border-emerald-700/50 py-1.5 rounded text-[10px] uppercase font-bold transition-colors">Accept</button>
            </div>
        </div>
        `;
    });
    grid.innerHTML = html;
}
function showLiveAnalysisModal(text) {
    let cleanText = text.replace(/```json/g, '').replace(/```/g, '').trim();
    alert("AI Analysis:\\n\\n" + cleanText);
}
async function acceptLiveActionCard(symbol, text) {
    try {
        let cleanText = text.trim();
        if (cleanText.startsWith('```json')) cleanText = cleanText.substring(7);
        else if (cleanText.startsWith('```')) cleanText = cleanText.substring(3);
        if (cleanText.endsWith('```')) cleanText = cleanText.substring(0, cleanText.length - 3);
        cleanText = cleanText.trim();
        
        let data = {};
        try {
            data = JSON.parse(cleanText);
        } catch (parseErr) {
            console.warn(`Fallback triggered: Failed to parse LLM JSON for ${symbol}. Assuming Wait action.`, parseErr);
            data = { Action: "Wait", Reason: "Fallback: " + cleanText, Confidence_Score: 0 };
        }
        
        const action = data.Action ? data.Action.toLowerCase() : "wait";
        const reason = data.Reason || data.Action || "Manual action accepted from UI.";
        const confidence = data.Confidence_Score || 5;

        if (action === "long" || action === "short") {
            const targetPrice = data.Entry_Target_Price || "";
            const executedPrice = prompt(`[Ledger] Confirm EXECUTION PRICE for ${symbol} (${action.toUpperCase()}):`, targetPrice);
            if (executedPrice === null) return;
            const executedQty = prompt(`[Ledger] Confirm QUANTITY for ${symbol}:`, "100");
            if (executedQty === null) return;
            
            await fetch("/api/ledger/open", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    symbol: symbol,
                    direction: action === "long" ? "Long" : "Short",
                    entry_price: parseFloat(executedPrice),
                    entry_qty: parseInt(executedQty),
                    confidence: confidence,
                    reason: reason
                })
            });
            
            const allPos = JSON.parse(localStorage.getItem('user_positions')) || {};
            allPos[symbol] = {
                entry_price: parseFloat(executedPrice),
                quantity: parseInt(executedQty),
                entry_timestamp: Math.floor(Date.now() / 1000),
                direction: action === "long" ? "Long" : "Short"
            };
            localStorage.setItem('user_positions', JSON.stringify(allPos));
            fetch('/api/reasoning/position/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol: symbol, user_position: allPos[symbol] })
            });
            
        } else if (action === "hold") {
            await fetch("/api/ledger/manage", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    symbol: symbol,
                    action: "Hold",
                    reason: reason
                })
            });
            
        } else if (action === "close") {
            const executedPrice = prompt(`[Ledger] Confirm CLOSING PRICE for ${symbol}:`, "");
            if (executedPrice === null) return;
            const executedQty = prompt(`[Ledger] Confirm CLOSING QUANTITY for ${symbol}:`, "100");
            if (executedQty === null) return;
            
            await fetch("/api/ledger/close", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    symbol: symbol,
                    exit_price: parseFloat(executedPrice),
                    exit_qty: parseInt(executedQty),
                    reason: reason
                })
            });
            
            const allPos = JSON.parse(localStorage.getItem('user_positions')) || {};
            delete allPos[symbol];
            localStorage.setItem('user_positions', JSON.stringify(allPos));
            fetch('/api/reasoning/position/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol: symbol, user_position: null })
            });
        }
        
        window.dismissedLiveReports[symbol] = text;
        pollLiveActionCards();
        
    } catch (e) {
        console.error("Error parsing action card JSON", e);
        alert("Failed to parse LLM reasoning payload. Raw text:\\n" + text);
    }
}
function openLiveSettingsModal(symbol) {
    const freq = prompt(`Enter Auto-Analyze frequency in seconds for ${symbol}:`, "60");
    if (freq) {
        const allPos = JSON.parse(localStorage.getItem('user_positions')) || {};
        const userPosition = allPos[symbol] || null;
        fetch(`${apiBase}/api/reasoning/loop/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: symbol, interval: parseInt(freq), user_position: userPosition })
        });
        alert(`Started auto-analyze for ${symbol} every ${freq} seconds.`);
    }
}
function toggleGlobalAutoAnalyze() {
    const isChecked = document.getElementById('global-auto-analyze').checked;
    const freq = document.getElementById('global-analyze-interval').value;
    const symbols = window.globalWatchlistSymbols || Object.keys(window.latestGlobalState || {});
    const allPos = JSON.parse(localStorage.getItem('user_positions')) || {};
    
    symbols.forEach(sym => {
        if (isChecked) {
            fetch(`${apiBase}/api/reasoning/loop/start`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol: sym, interval: parseInt(freq), user_position: allPos[sym] || null })
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

