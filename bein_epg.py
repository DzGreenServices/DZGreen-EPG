from playwright.sync_api import sync_playwright


URL = "https://www.bein.com/ar/%d8%ac%d8%af%d9%88%d9%84-%d8%a7%d9%84%d8%a8%d8%ab/?c=dz&"


def main():

    print("=" * 60)
    print("DZGreen - beIN Official TV Guide Test")
    print("=" * 60)

    with sync_playwright() as p:

        print("Launching Chromium...")

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            locale="ar-DZ"
        )

        print("Opening official beIN website...")

        page.goto(
            URL,
            wait_until="domcontentloaded",
            timeout=120000
        )

        print("Page loaded.")

        # Give the website JavaScript time to build the TV guide
        page.wait_for_timeout(10000)

        print()
        print("Checking official TV guide...")
        print()

        day_cells = page.locator(
            ".day-cell"
        ).count()

        channel_rows = page.locator(
            ".channel-row"
        ).count()

        program_blocks = page.locator(
            ".prog-block"
        ).count()

        print("day-cell     :", day_cells)
        print("channel-row  :", channel_rows)
        print("prog-block   :", program_blocks)

        print()

        # ----------------------------------------------------
        # Show available dates
        # ----------------------------------------------------

        if day_cells > 0:

            print("Available dates:")

            for i in range(day_cells):

                element = page.locator(
                    ".day-cell"
                ).nth(i)

                date = element.get_attribute(
                    "data-date"
                )

                print(
                    "  -",
                    date
                )

        else:

            print(
                "No day-cell elements found."
            )

        print()

        # ----------------------------------------------------
        # Show first channel
        # ----------------------------------------------------

        if channel_rows > 0:

            first_channel = page.locator(
                ".channel-row"
            ).first

            link = first_channel.locator(
                ".channel-col a"
            ).first

            href = link.get_attribute(
                "href"
            )

            image = link.locator(
                "img"
            ).first

            logo = image.get_attribute(
                "src"
            )

            print("First channel:")
            print("  URL  :", href)
            print("  Logo :", logo)

        else:

            print(
                "No channel-row elements found."
            )

        print()

        # ----------------------------------------------------
        # Show first program
        # ----------------------------------------------------

        if program_blocks > 0:

            first_program = page.locator(
                ".prog-block"
            ).first

            print("First program:")

            print(
                "  Title    :",
                first_program.get_attribute(
                    "data-full-title"
                )
            )

            print(
                "  Category :",
                first_program.get_attribute(
                    "data-full-category"
                )
            )

            print(
                "  Time     :",
                first_program.get_attribute(
                    "data-full-time"
                )
            )

            print(
                "  Start MS :",
                first_program.get_attribute(
                    "data-start-ms"
                )
            )

            print(
                "  End MS   :",
                first_program.get_attribute(
                    "data-end-ms"
                )
            )

        else:

            print(
                "No prog-block elements found."
            )

        print()

        print("=" * 60)
        print("Test finished")
        print("=" * 60)

        browser.close()


if __name__ == "__main__":
    main()
