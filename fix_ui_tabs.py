with open('trading_copilot/templates/index.html', 'r', encoding='utf-8') as f:
    text = f.read()

# Extract the entire content of Modal Body, starting from <div class="p-5 overflow-y-auto flex-1 bg-slate-950 flex flex-col gap-4">
# to the end of the modal body block.

old_body_start = '''            <!-- Modal Body -->
            <div class="p-5 overflow-y-auto flex-1 bg-slate-950 flex flex-col gap-4">
                
                <!-- AI Reasoning Output -->
                <div class="bg-[#0d1117] rounded-lg p-4 border border-slate-700 h-64 overflow-y-auto shadow-inner">
                    <pre><code id="reasoning-report-block" class="text-xs font-mono-custom text-slate-300 whitespace-pre-wrap">Awaiting trigger...</code></pre>
                </div>'''

# The rest of the old body is the Intent Inputs and Raw JSON Payload. We'll reconstruct it entirely.
old_body_full = '''            <!-- Modal Body -->
            <div class="p-5 overflow-y-auto flex-1 bg-slate-950 flex flex-col gap-4">
                
                <!-- AI Reasoning Output -->
                <div class="bg-[#0d1117] rounded-lg p-4 border border-slate-700 h-64 overflow-y-auto shadow-inner">
                    <pre><code id="reasoning-report-block" class="text-xs font-mono-custom text-slate-300 whitespace-pre-wrap">Awaiting trigger...</code></pre>
                </div>

                                    <!-- Intent Inputs -->
                    <div class="flex flex-wrap items-center gap-4 mb-2 pt-3 border-t border-slate-700/50">
                        <span class="text-xs font-bold text-slate-400 uppercase">Optional: Proposed Trade</span>
                        <div class="flex items-center gap-2">
                            <span class="text-xs text-slate-400">Action:</span>
                            <select id="intent-action" class="bg-slate-800 text-xs text-slate-200 border border-slate-700 rounded px-2 py-1 focus:outline-none focus:border-indigo-500">
                                <option value="None" selected>None</option>
                                <option value="Buy Market">Buy Market</option>
                                <option value="Buy Limit">Buy Limit</option>
                                <option value="Sell Market">Sell Market</option>
                                <option value="Sell Limit">Sell Limit</option>
                            </select>
                        </div>
                        <div class="flex items-center gap-2">
                            <span class="text-xs text-slate-400">Type:</span>
                            <select id="intent-type" class="bg-slate-800 text-xs text-slate-200 border border-slate-700 rounded px-2 py-1 focus:outline-none focus:border-indigo-500">
                                <option value="Intraday" selected>Intraday</option>
                                <option value="Delivery">Delivery</option>
                            </select>
                        </div>
                        <div class="flex items-center gap-2">
                            <span class="text-xs text-slate-400">Qty:</span>
                            <input type="number" id="intent-qty" placeholder="e.g. 100" class="w-16 bg-slate-800 text-xs text-slate-200 border border-slate-700 rounded px-2 py-1 focus:outline-none focus:border-indigo-500">
                        </div>
                        <div class="flex items-center gap-2">
                            <span class="text-xs text-slate-400">Price:</span>
                            <input type="number" id="intent-price" placeholder="e.g. 150.5" class="w-16 bg-slate-800 text-xs text-slate-200 border border-slate-700 rounded px-2 py-1 focus:outline-none focus:border-indigo-500">
                        </div>
                    </div>
                    <div class="flex flex-wrap items-center gap-4 mb-4">
                        <div class="flex items-center gap-2 flex-1">
                            <span class="text-xs text-slate-400">Advice:</span>
                            <input type="text" id="intent-advice" placeholder="e.g. Scaling in slowly on dips..." class="flex-1 bg-slate-800 text-xs text-slate-200 border border-slate-700 rounded px-2 py-1 focus:outline-none focus:border-indigo-500">
                        </div>
                        <button id="btn-inject-intent" onclick="copyToClipboard()" class="px-4 py-1 text-xs font-semibold rounded bg-purple-600 text-white hover:bg-purple-500 transition-colors shadow-lg">Inject & Copy</button>
                    </div>
                <!-- Raw JSON Payload -->
                <div class="bg-[#0d1117] border border-slate-800 rounded-lg p-4 h-96 overflow-y-auto">
                    <h4 class="text-xs font-bold text-slate-500 mb-2 uppercase">Raw Telemetry JSON</h4>
                    <pre><code id="modal-code-block" class="text-xs font-mono-custom text-emerald-400 whitespace-pre-wrap break-all"></code></pre>
                </div>
            </div>'''

new_body_full = '''            <!-- Modal Body -->
            <div class="p-5 overflow-hidden flex-1 bg-slate-950 flex flex-col gap-4">
                
                <!-- Tab Navigation -->
                <div class="flex items-center gap-2 border-b border-slate-800 pb-2">
                    <button id="tab-btn-reasoning" onclick="switchModalTab('reasoning')" class="px-4 py-2 text-xs font-bold uppercase tracking-wider rounded-t-lg bg-slate-800 text-indigo-400 border-t border-l border-r border-slate-700 transition-colors">AI Reasoning</button>
                    <button id="tab-btn-catalyst" onclick="switchModalTab('catalyst')" class="px-4 py-2 text-xs font-bold uppercase tracking-wider rounded-t-lg bg-transparent text-slate-500 hover:text-slate-300 transition-colors">News Catalyst</button>
                    <button id="tab-btn-telemetry" onclick="switchModalTab('telemetry')" class="px-4 py-2 text-xs font-bold uppercase tracking-wider rounded-t-lg bg-transparent text-slate-500 hover:text-slate-300 transition-colors">Raw Telemetry</button>
                </div>

                <!-- Tab Content: Reasoning -->
                <div id="tab-content-reasoning" class="flex flex-col gap-4 flex-1 overflow-y-auto pr-2">
                    <!-- AI Reasoning Output -->
                    <div class="bg-[#0d1117] rounded-lg p-4 border border-slate-700 h-[300px] overflow-y-auto shadow-inner">
                        <pre><code id="reasoning-report-block" class="text-xs font-mono-custom text-slate-300 whitespace-pre-wrap">Awaiting trigger...</code></pre>
                    </div>

                    <!-- Intent Inputs -->
                    <div class="flex flex-wrap items-center gap-4 pt-3 border-t border-slate-700/50">
                        <span class="text-xs font-bold text-slate-400 uppercase">Optional: Proposed Trade</span>
                        <div class="flex items-center gap-2">
                            <span class="text-xs text-slate-400">Action:</span>
                            <select id="intent-action" class="bg-slate-800 text-xs text-slate-200 border border-slate-700 rounded px-2 py-1 focus:outline-none focus:border-indigo-500">
                                <option value="None" selected>None</option>
                                <option value="Buy Market">Buy Market</option>
                                <option value="Buy Limit">Buy Limit</option>
                                <option value="Sell Market">Sell Market</option>
                                <option value="Sell Limit">Sell Limit</option>
                            </select>
                        </div>
                        <div class="flex items-center gap-2">
                            <span class="text-xs text-slate-400">Type:</span>
                            <select id="intent-type" class="bg-slate-800 text-xs text-slate-200 border border-slate-700 rounded px-2 py-1 focus:outline-none focus:border-indigo-500">
                                <option value="Intraday" selected>Intraday</option>
                                <option value="Delivery">Delivery</option>
                            </select>
                        </div>
                        <div class="flex items-center gap-2">
                            <span class="text-xs text-slate-400">Qty:</span>
                            <input type="number" id="intent-qty" placeholder="e.g. 100" class="w-16 bg-slate-800 text-xs text-slate-200 border border-slate-700 rounded px-2 py-1 focus:outline-none focus:border-indigo-500">
                        </div>
                        <div class="flex items-center gap-2">
                            <span class="text-xs text-slate-400">Price:</span>
                            <input type="number" id="intent-price" placeholder="e.g. 150.5" class="w-16 bg-slate-800 text-xs text-slate-200 border border-slate-700 rounded px-2 py-1 focus:outline-none focus:border-indigo-500">
                        </div>
                    </div>
                    <div class="flex flex-wrap items-center gap-4 pb-2">
                        <div class="flex items-center gap-2 flex-1">
                            <span class="text-xs text-slate-400">Advice:</span>
                            <input type="text" id="intent-advice" placeholder="e.g. Scaling in slowly on dips..." class="flex-1 bg-slate-800 text-xs text-slate-200 border border-slate-700 rounded px-2 py-1 focus:outline-none focus:border-indigo-500">
                        </div>
                        <button id="btn-inject-intent" onclick="copyToClipboard()" class="px-4 py-1 text-xs font-semibold rounded bg-purple-600 text-white hover:bg-purple-500 transition-colors shadow-lg">Inject & Copy</button>
                    </div>
                </div>

                <!-- Tab Content: Catalyst -->
                <div id="tab-content-catalyst" class="hidden flex-col gap-4 flex-1 overflow-y-auto pr-2">
                    <div class="bg-[#0d1117] rounded-lg p-4 border border-slate-700 h-[400px] overflow-y-auto shadow-inner relative">
                        <div class="flex justify-between items-center mb-4 border-b border-slate-800 pb-2 sticky top-0 bg-[#0d1117] z-10 pt-1">
                            <h4 class="text-xs font-bold text-slate-500 uppercase">Live News Catalyst</h4>
                            <button id="btn-force-news" onclick="forceFetchNews()" class="px-3 py-1 bg-blue-600 hover:bg-blue-500 text-white text-[10px] uppercase font-bold rounded shadow transition-colors flex items-center gap-2">
                                📰 Force Fetch News
                            </button>
                        </div>
                        <pre><code id="catalyst-report-block" class="text-xs font-mono-custom text-slate-300 whitespace-pre-wrap">Loading catalyst data...</code></pre>
                    </div>
                </div>

                <!-- Tab Content: Telemetry -->
                <div id="tab-content-telemetry" class="hidden flex-col gap-4 flex-1 overflow-y-auto pr-2">
                    <div class="bg-[#0d1117] border border-slate-800 rounded-lg p-4 h-[400px] overflow-y-auto">
                        <h4 class="text-xs font-bold text-slate-500 mb-2 uppercase sticky top-0 bg-[#0d1117] z-10 pt-1 pb-2">Raw Telemetry JSON</h4>
                        <pre><code id="modal-code-block" class="text-xs font-mono-custom text-emerald-400 whitespace-pre-wrap break-all"></code></pre>
                    </div>
                </div>

            </div>'''

if old_body_full in text:
    text = text.replace(old_body_full, new_body_full)
    print("Replaced modal body with tabs successfully.")
else:
    print("Warning: old body not found.")


# Inject the JS for tabs and fetching news
js_inject = '''        // --- Modal Tabs Logic ---
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
                    let out = `--- RAW NEWS FETCHED ---\\n`;
                    if (data.raw_news && data.raw_news.length > 0) {
                        data.raw_news.forEach((n, i) => {
                            out += `\\n[Article ${i+1}] ${n.heading}\\n${n.summary}\\nUrl: ${n.url}\\n`;
                        });
                    } else {
                        out += `\\nNo news articles found for ${symbol}.\\n`;
                    }
                    
                    out += `\\n--- AI CATALYST SUMMARY ---\\n`;
                    out += `\\n${data.catalyst_summary || "No summary generated."}\\n`;
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
'''

if 'function switchModalTab' not in text:
    text = text.replace('        function closeJsonModal() {', js_inject + '\n        function closeJsonModal() {')
    print("Injected JS successfully.")

# When modal opens, populate catalyst block from latest state and reset tab
reset_tab_js = '''            switchModalTab('reasoning');
            const catalystBlock = document.getElementById('catalyst-report-block');
            if (payloadCopy.latest_catalyst) {
                catalystBlock.innerText = "Current Cached Catalyst Summary:\\n\\n" + payloadCopy.latest_catalyst;
            } else {
                catalystBlock.innerText = "No cached news catalyst available for this asset. Click 'Force Fetch News' to query live API.";
            }'''

if "switchModalTab('reasoning');" not in text:
    text = text.replace("document.getElementById('llm-modal').classList.remove('hidden');", reset_tab_js + "\n            document.getElementById('llm-modal').classList.remove('hidden');")
    print("Injected openJsonModal hook successfully.")

with open('trading_copilot/templates/index.html', 'w', encoding='utf-8') as f:
    f.write(text)

print('Done.')
