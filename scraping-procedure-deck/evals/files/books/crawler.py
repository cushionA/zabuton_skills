import csv
from datetime import datetime

from playwright.sync_api import sync_playwright

START = "https://books.toscrape.com/"


def scrape(page):
    rows = []
    page.goto(START)
    page.get_by_role("link", name="Mystery").click()
    while True:
        links = page.locator("article.product_pod h3 a")
        for i in range(links.count()):
            links.nth(i).click()
            info = page.locator("table.table-striped")
            rows.append({
                "crawled_at": datetime.now().isoformat(timespec="seconds"),
                "url": page.url,
                "category": page.locator("ul.breadcrumb li").nth(2).inner_text(),
                "title": page.locator("div.product_main h1").inner_text(),
                "price": page.locator("div.product_main p.price_color").inner_text(),
                "stock": page.locator("div.product_main p.availability").inner_text(),
                "rating": page.locator("div.product_main p.star-rating").get_attribute("class"),
                "upc": info.locator("tr", has_text="UPC").locator("td").inner_text(),
                "reviews": info.locator("tr", has_text="Number of reviews").locator("td").inner_text(),
            })
            page.go_back()
        next_link = page.locator("li.next a")
        if next_link.count() == 0:
            break
        next_link.click()
    return rows


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        rows = scrape(page)
        browser.close()
    with open("books.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
