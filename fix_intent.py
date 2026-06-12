with open('trading_copilot/templates/index.html', 'r', encoding='utf-8') as f:
    text = f.read()

new_ui = '''                    <!-- Intent Inputs -->
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
                    </div>'''

if 'id="btn-inject-intent"' not in text:
    text = text.replace('<!-- Raw JSON Payload -->', new_ui + '\n                <!-- Raw JSON Payload -->')
    print('Inserted Intent UI HTML')

new_fn = '''        function getUserIntent() {
            const action = document.getElementById('intent-action').value;
            if (action === "None") return null;
            const type = document.getElementById('intent-type').value;
            const advice = document.getElementById('intent-advice').value;
            const qty = parseFloat(document.getElementById('intent-qty').value) || 0;
            const price = parseFloat(document.getElementById('intent-price').value) || 0;
            return { action: action, type: type, quantity: qty, price: price, advice: advice };
        }'''

if 'function getUserIntent()' not in text:
    text = text.replace('function copyToClipboard()', new_fn + '\n\n        function copyToClipboard()')
    print('Inserted getUserIntent JS')

old_copy = 'const finalClipboardText = `${statusText}${currentPrompt}\\n\\n--- RAW TELEMETRY DATA ---\\n\\n${codeBlock}`;'
new_copy = '''            
            let intentText = '';
            const intent = getUserIntent();
            if (intent) {
                intentText = `Proposed Trade Intent: ${intent.action} (${intent.type}) | Qty: ${intent.quantity} | Price: ${intent.price}\\nAdvice/Note: ${intent.advice || 'None'}\\n\\n`;
            }

            const finalClipboardText = `${statusText}${intentText}${currentPrompt}\\n\\n--- RAW TELEMETRY DATA ---\\n\\n${codeBlock}`;'''

if 'const intent = getUserIntent();' not in text:
    text = text.replace(old_copy, new_copy)
    print('Replaced copyToClipboard to include intent')

with open('trading_copilot/templates/index.html', 'w', encoding='utf-8') as f:
    f.write(text)

print('Done.')
