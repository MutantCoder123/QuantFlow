import asyncio
from playwright.async_api import async_playwright

async def main():
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto('http://127.0.0.1:8000', wait_until='networkidle')
            await asyncio.sleep(2)
            await page.click('#tab-btn-discovery')
            await asyncio.sleep(2)
            html = await page.evaluate('document.getElementById("tab-discovery").innerHTML')
            with open('tab_html3.txt', 'w', encoding='utf-8') as f:
                f.write(html)
            await browser.close()
    except Exception as e:
        with open('tab_html3.txt', 'w', encoding='utf-8') as f:
            f.write(str(e))

asyncio.run(main())
