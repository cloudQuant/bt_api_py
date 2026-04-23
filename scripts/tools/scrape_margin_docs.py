#!/usr/bin/env python3
"""Scrape Binance Margin Trading API docs using Playwright and save as markdown files."""

import os
import time

from playwright.sync_api import sync_playwright

BASE_URL = "https://developers.binance.com"
DOCS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "binance", "margin_trading"
)

# All known pages from sidebar exploration
ALL_PAGES = {
    # Top-level pages
    "change-log": "/docs/margin_trading/change-log",
    "general-info": "/docs/margin_trading/general-info",
    "introduction": "/docs/margin_trading/Introduction",
    "common-definition": "/docs/margin_trading/common-definition",
    "error-code": "/docs/margin_trading/error-code",
    "best-practice": "/docs/margin_trading/best-practice",
    # Market Data
    "market-data/cross-margin-collateral-ratio": "/docs/margin_trading/market-data",
    "market-data/get-all-cross-margin-pairs": "/docs/margin_trading/market-data/Get-All-Cross-Margin-Pairs",
    "market-data/get-all-isolated-margin-symbol": "/docs/margin_trading/market-data/Get-All-Isolated-Margin-Symbol",
    "market-data/get-all-margin-assets": "/docs/margin_trading/market-data/Get-All-Margin-Assets",
    "market-data/get-delist-schedule": "/docs/margin_trading/market-data/Get-Delist-Schedule",
    "market-data/get-list-schedule": "/docs/margin_trading/market-data/Get-List-Schedule",
    "market-data/get-limit-price-pairs": "/docs/margin_trading/market-data/Get-Limit-Price-Pairs",
    "market-data/get-margin-asset-risk-based-liquidation-ratio": "/docs/margin_trading/market-data/Get-Margin-Asset-Risk-Based-Liquidation-Ratio",
    "market-data/query-isolated-margin-tier-data": "/docs/margin_trading/market-data/Query-Isolated-Margin-Tier-Data",
    "market-data/query-margin-priceindex": "/docs/margin_trading/market-data/Query-Margin-PriceIndex",
    "market-data/query-margin-available-inventory": "/docs/margin_trading/market-data/Query-margin-avaliable-inventory",
    "market-data/query-liability-coin-leverage-bracket": "/docs/margin_trading/market-data/Query-Liability-Coin-Leverage-Bracket-in-Cross-Margin-Pro-Mode",
    "market-data/get-margin-restricted-assets": "/docs/margin_trading/market-data/Get-Margin-Restricted-Assets",
    # Borrow And Repay
    "borrow-and-repay/get-future-hourly-interest-rate": "/docs/margin_trading/borrow-and-repay",
    "borrow-and-repay/get-interest-history": "/docs/margin_trading/borrow-and-repay/Get-Interest-History",
    "borrow-and-repay/margin-account-borrow-repay": "/docs/margin_trading/borrow-and-repay/Margin-Account-Borrow-Repay",
    "borrow-and-repay/query-borrow-repay": "/docs/margin_trading/borrow-and-repay/Query-Borrow-Repay",
    "borrow-and-repay/query-margin-interest-rate-history": "/docs/margin_trading/borrow-and-repay/Query-Margin-Interest-Rate-History",
    "borrow-and-repay/query-max-borrow": "/docs/margin_trading/borrow-and-repay/Query-Max-Borrow",
    # Trade
    "trade/margin-account-new-order": "/docs/margin_trading/trade/Margin-Account-New-Order",
    "trade/margin-account-cancel-order": "/docs/margin_trading/trade/Margin-Account-Cancel-Order",
    "trade/margin-account-cancel-all-open-orders": "/docs/margin_trading/trade/Margin-Account-Cancel-all-Open-Orders-on-a-Symbol",
    "trade/query-margin-account-order": "/docs/margin_trading/trade/Query-Margin-Account-Order",
    "trade/query-margin-account-open-orders": "/docs/margin_trading/trade/Query-Margin-Account-Open-Orders",
    "trade/query-margin-account-all-orders": "/docs/margin_trading/trade/Query-Margin-Account-All-Orders",
    "trade/margin-account-new-oco": "/docs/margin_trading/trade/Margin-Account-New-OCO",
    "trade/margin-account-cancel-oco": "/docs/margin_trading/trade/Margin-Account-Cancel-OCO",
    "trade/query-margin-account-oco": "/docs/margin_trading/trade/Query-Margin-Account-OCO",
    "trade/query-margin-account-all-oco": "/docs/margin_trading/trade/Query-Margin-Account-All-OCO",
    "trade/query-margin-account-open-oco": "/docs/margin_trading/trade/Query-Margin-Account-Open-OCO",
    "trade/margin-account-new-oto": "/docs/margin_trading/trade/Margin-Account-New-OTO",
    "trade/margin-account-new-otoco": "/docs/margin_trading/trade/Margin-Account-New-OTOCO",
    "trade/query-margin-account-trade-list": "/docs/margin_trading/trade/Query-Margin-Account-Trade-List",
    "trade/query-current-margin-order-count-usage": "/docs/margin_trading/trade/Query-Current-Margin-Order-Count-Usage",
    "trade/small-liability-exchange": "/docs/margin_trading/trade/Small-Liability-Exchange",
    "trade/get-small-liability-exchange-coin-list": "/docs/margin_trading/trade/Get-Small-Liability-Exchange-Coin-List",
    "trade/get-small-liability-exchange-history": "/docs/margin_trading/trade/Get-Small-Liability-Exchange-History",
    "trade/margin-manual-liquidation": "/docs/margin_trading/trade/Margin-Manual-Liquidation",
    "trade/query-margin-prevented-matches": "/docs/margin_trading/trade/Query-Margin-Prevented-Matches",
    "trade/query-margin-allocations": "/docs/margin_trading/trade/Query-Margin-Allocations",
    "trade/query-special-key-of-low-latency-trading": "/docs/margin_trading/trade/Query-Special-Key-of-Low-Latency-Trading",
    "trade/query-special-key-list-of-low-latency-trading": "/docs/margin_trading/trade/Query-Special-Key-List-of-Low-Latency-Trading",
    # Transfer
    "transfer/cross-margin-transfer": "/docs/margin_trading/transfer",
    "transfer/query-max-transfer-out-amount": "/docs/margin_trading/transfer/Query-Max-Transfer-Out-Amount",
    # Account
    "account/query-cross-margin-account-details": "/docs/margin_trading/account/Query-Cross-Margin-Account-Details",
    "account/get-summary-of-margin-account": "/docs/margin_trading/account/Get-Summary-Of-Margin-Account",
    "account/query-isolated-margin-account-info": "/docs/margin_trading/account/Query-Isolated-Margin-Account-Info",
    "account/enable-isolated-margin-account": "/docs/margin_trading/account/Enable-Isolated-Margin-Account",
    "account/disable-isolated-margin-account": "/docs/margin_trading/account/Disable-Isolated-Margin-Account",
    "account/query-enabled-isolated-margin-account-limit": "/docs/margin_trading/account/Query-Enabled-Isolated-Margin-Account-Limit",
    "account/query-cross-margin-fee-data": "/docs/margin_trading/account/Query-Cross-Margin-Fee-Data",
    "account/query-isolated-margin-fee-data": "/docs/margin_trading/account/Query-Isolated-Margin-Fee-Data",
    "account/query-cross-isolated-margin-capital-flow": "/docs/margin_trading/account/Query-Cross-Isolated-Margin-Capital-Flow",
    "account/get-bnb-burn-status": "/docs/margin_trading/account/Get-BNB-Burn-Status",
    "account/toggle-bnb-burn": "/docs/margin_trading/account",
    # Trade Data Stream
    "trade-data-stream/create-listen-token": "/docs/margin_trading/trade-data-stream",
    "trade-data-stream/websocket-subscribe": "/docs/margin_trading/trade-data-stream/Websocket-Subscribe-User-Data-Stream-using-Listen-Token",
    "trade-data-stream/payload-account-update": "/docs/margin_trading/trade-data-stream/Payload-Account-Update",
    # Risk Data Stream
    "risk-data-stream/overview": "/docs/margin_trading/risk-data-stream",
    "risk-data-stream/create-listen-key": "/docs/margin_trading/risk-data-stream/Create-a-ListenKey",
    "risk-data-stream/keepalive-listen-key": "/docs/margin_trading/risk-data-stream/Keepalive-a-ListenKey",
    "risk-data-stream/close-listen-key": "/docs/margin_trading/risk-data-stream/Close-a-ListenKey",
    "risk-data-stream/payload": "/docs/margin_trading/risk-data-stream/Payload",
}


def extract_content_as_markdown(page):
    """Use JavaScript to extract article content and convert to markdown."""
    return page.evaluate("""() => {
        function inlineToMd(el) {
            let out = '';
            for (const child of el.childNodes) {
                if (child.nodeType === 3) { out += child.textContent; continue; }
                if (child.nodeType !== 1) continue;
                const tag = child.tagName.toLowerCase();
                if (tag === 'code') out += '`' + child.textContent + '`';
                else if (tag === 'strong' || tag === 'b') out += '**' + child.textContent + '**';
                else if (tag === 'em' || tag === 'i') out += '*' + child.textContent + '*';
                else if (tag === 'a') {
                    const href = child.getAttribute('href') || '';
                    // Skip "Direct link to" anchors
                    if (href.startsWith('#') && child.textContent.trim() === '#') continue;
                    out += '[' + child.textContent + '](' + href + ')';
                }
                else if (tag === 'br') out += '\\n';
                else out += inlineToMd(child);
            }
            return out;
        }
        
        function listToMd(el, indent) {
            indent = indent || 0;
            const ordered = el.tagName.toLowerCase() === 'ol';
            const prefix = '  '.repeat(indent);
            let lines = [];
            let idx = 0;
            for (const li of el.querySelectorAll(':scope > li')) {
                idx++;
                const marker = ordered ? idx + '.' : '-';
                let textParts = [];
                let subLists = [];
                for (const child of li.childNodes) {
                    if (child.nodeType === 3) {
                        const t = child.textContent.trim();
                        if (t) textParts.push(t);
                    } else if (child.nodeType === 1) {
                        const ctag = child.tagName.toLowerCase();
                        if (ctag === 'ul' || ctag === 'ol') {
                            subLists.push(child);
                        } else if (ctag === 'p') {
                            textParts.push(inlineToMd(child));
                        } else if (ctag === 'pre' || (ctag === 'div' && child.querySelector('pre'))) {
                            const codeEl = child.querySelector('code') || child.querySelector('pre');
                            if (codeEl) {
                                const lang = Array.from(codeEl.classList).find(c => c.startsWith('language-'));
                                const langStr = lang ? lang.replace('language-', '') : '';
                                textParts.push('\\n```' + langStr + '\\n' + codeEl.textContent + '\\n```');
                            }
                        } else if (ctag === 'code') {
                            textParts.push('`' + child.textContent + '`');
                        } else if (ctag === 'strong' || ctag === 'b') {
                            textParts.push('**' + child.textContent + '**');
                        } else if (ctag === 'a') {
                            textParts.push('[' + child.textContent + '](' + (child.getAttribute('href') || '') + ')');
                        } else {
                            textParts.push(inlineToMd(child));
                        }
                    }
                }
                const text = textParts.join(' ').trim();
                lines.push(prefix + marker + ' ' + text);
                for (const sl of subLists) {
                    lines.push(listToMd(sl, indent + 1));
                }
            }
            return lines.join('\\n');
        }
        
        function tableToMd(table) {
            const rows = [];
            for (const tr of table.querySelectorAll('tr')) {
                const cells = [];
                for (const td of tr.querySelectorAll('th, td')) {
                    cells.push(td.textContent.trim().replace(/\\|/g, '\\\\|').replace(/\\n/g, ' '));
                }
                if (cells.length) rows.push(cells);
            }
            if (!rows.length) return '';
            const maxCols = Math.max(...rows.map(r => r.length));
            rows.forEach(r => { while (r.length < maxCols) r.push(''); });
            let lines = [];
            lines.push('| ' + rows[0].join(' | ') + ' |');
            lines.push('| ' + rows[0].map(() => '---').join(' | ') + ' |');
            for (let i = 1; i < rows.length; i++) {
                lines.push('| ' + rows[i].join(' | ') + ' |');
            }
            return '\\n' + lines.join('\\n') + '\\n';
        }
        
        function convertToMd(el) {
            let lines = [];
            for (const child of el.childNodes) {
                if (child.nodeType === 3) {
                    const t = child.textContent.trim();
                    if (t) lines.push(t);
                    continue;
                }
                if (child.nodeType !== 1) continue;
                const tag = child.tagName.toLowerCase();
                
                if (/^h[1-6]$/.test(tag)) {
                    const level = parseInt(tag[1]);
                    let text = child.textContent.trim();
                    // Remove "Direct link to ..." suffix
                    text = text.replace(/Direct link to .+$/, '').trim();
                    // Remove trailing #
                    text = text.replace(/#$/, '').trim();
                    lines.push('\\n' + '#'.repeat(level) + ' ' + text + '\\n');
                }
                else if (tag === 'p') {
                    lines.push('\\n' + inlineToMd(child) + '\\n');
                }
                else if (tag === 'ul' || tag === 'ol') {
                    lines.push(listToMd(child));
                }
                else if (tag === 'pre' || (tag === 'div' && child.querySelector('pre'))) {
                    const codeEl = child.querySelector('code') || child.querySelector('pre');
                    if (codeEl) {
                        const lang = Array.from(codeEl.classList).find(c => c.startsWith('language-'));
                        const langStr = lang ? lang.replace('language-', '') : '';
                        lines.push('\\n```' + langStr + '\\n' + codeEl.textContent + '\\n```\\n');
                    }
                }
                else if (tag === 'table') {
                    lines.push(tableToMd(child));
                }
                else if (tag === 'blockquote') {
                    lines.push('\\n> ' + child.textContent.trim() + '\\n');
                }
                else if (tag === 'hr') {
                    lines.push('\\n---\\n');
                }
                else if (tag === 'div' || tag === 'section' || tag === 'article' || tag === 'main') {
                    // Check for admonition/alert classes
                    const classes = child.className || '';
                    if (classes.includes('admonition') || classes.includes('alert')) {
                        const title = child.querySelector('.admonition-heading, .alert-heading');
                        const titleText = title ? title.textContent.trim() : 'Note';
                        const body = child.querySelector('.admonition-content') || child;
                        lines.push('\\n> **' + titleText + ':** ' + body.textContent.trim() + '\\n');
                    } else if (classes.includes('tabs')) {
                        // Handle tab components - just convert children
                        lines.push(convertToMd(child));
                    } else {
                        lines.push(convertToMd(child));
                    }
                }
                else if (tag === 'details') {
                    const summary = child.querySelector('summary');
                    const summaryText = summary ? summary.textContent.trim() : 'Details';
                    lines.push('\\n<details>\\n<summary>' + summaryText + '</summary>\\n\\n' + child.textContent.trim() + '\\n</details>\\n');
                }
                else {
                    const t = child.textContent.trim();
                    if (t) lines.push(t);
                }
            }
            return lines.join('\\n');
        }
        
        // Find the article element
        const article = document.querySelector('article');
        if (!article) return '';
        
        // Remove navigation elements (breadcrumbs, pagination)
        article.querySelectorAll('nav').forEach(n => n.remove());
        
        // Get the content div (usually the second child after breadcrumbs)
        const contentDiv = article.querySelector('.markdown, [class*="docItemContent"], [class*="mdxContent"]') || article;
        
        return convertToMd(contentDiv);
    }""")


def discover_sidebar_links(page):
    """Expand all sidebar sections and extract all links."""
    return page.evaluate("""() => {
        const sidebar = document.querySelector('nav[aria-label="Docs sidebar"]');
        if (!sidebar) return [];
        const links = sidebar.querySelectorAll('a');
        const result = [];
        links.forEach(a => {
            const href = a.getAttribute('href');
            const text = a.textContent.trim();
            if (href && href.startsWith('/docs/margin_trading') && href !== '/docs/margin_trading') {
                result.push({text, href});
            }
        });
        return result;
    }""")


def main():
    os.makedirs(DOCS_DIR, exist_ok=True)

    print(f"Total pages to scrape: {len(ALL_PAGES)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        page = context.new_page()
        page.set_default_timeout(30000)

        scraped = {}
        failed = []

        for filename, url_path in sorted(ALL_PAGES.items()):
            full_url = BASE_URL + url_path
            print(f"  [{len(scraped) + 1}/{len(ALL_PAGES)}] {filename}")
            try:
                page.goto(full_url, wait_until="networkidle", timeout=30000)
                page.wait_for_selector("article", timeout=10000)
                time.sleep(0.5)  # Extra wait for dynamic content

                content = extract_content_as_markdown(page)
                if content and len(content.strip()) > 20:
                    scraped[filename] = content
                else:
                    print(f"    WARNING: Very short content ({len(content)} chars)")
                    scraped[filename] = content

                # Try to discover new links from sidebar
                try:
                    links = discover_sidebar_links(page)
                    for link in links:
                        href = link["href"]
                        key = href.replace("/docs/margin_trading/", "").lower().rstrip("/")
                        if key and key not in ALL_PAGES and key not in scraped:
                            print(f"    Discovered: {key} -> {href}")
                except Exception:
                    pass

            except Exception as e:
                print(f"    ERROR: {e}")
                failed.append(filename)

            time.sleep(0.3)

        browser.close()

    # Save markdown files
    print(f"\nSaving {len(scraped)} markdown files...")
    for filename, content in sorted(scraped.items()):
        filepath = os.path.join(DOCS_DIR, filename + ".md")
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  Saved: {filepath} ({len(content)} bytes)")

    if failed:
        print(f"\nFailed pages ({len(failed)}):")
        for f in failed:
            print(f"  {f}")

    print(f"\nDone! {len(scraped)} files saved to {DOCS_DIR}")

    # Summary
    sections = {}
    for key in sorted(scraped.keys()):
        parts = key.split("/")
        section = parts[0] if len(parts) > 1 else "(top-level)"
        sections.setdefault(section, []).append(key)

    print("\nSummary:")
    for section, pages in sorted(sections.items()):
        print(f"  {section}: {len(pages)} pages")


if __name__ == "__main__":
    main()
