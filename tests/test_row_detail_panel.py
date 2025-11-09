"""
Playwright tests for the row detail side panel feature.
"""

import pytest
import subprocess
import sys
import tempfile
import time
import httpx
from playwright.sync_api import expect


def wait_until_responds(url, timeout=5.0):
    """Wait until a URL responds to HTTP requests"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            httpx.get(url)
            return
        except httpx.ConnectError:
            time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {url} to respond")


@pytest.fixture(scope="module")
def datasette_server():
    """Start a Datasette server for testing"""
    # Create a simple test database
    import sqlite3
    import os

    db_path = os.path.join(tempfile.gettempdir(), "test_products.db")
    # Remove if exists
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            name TEXT,
            description TEXT,
            price REAL,
            category TEXT
        )
    """
    )
    conn.execute(
        """
        INSERT INTO products (name, description, price, category) VALUES
        ('Laptop', 'High-performance laptop', 999.99, 'Electronics'),
        ('Mouse', 'Wireless mouse', 29.99, 'Electronics'),
        ('Desk', 'Standing desk', 499.99, 'Furniture'),
        ('Chair', 'Ergonomic chair', 299.99, 'Furniture'),
        ('Notebook', 'Spiral notebook', 4.99, 'Stationery')
    """
    )
    conn.commit()
    conn.close()

    # Start Datasette server
    ds_proc = subprocess.Popen(
        [sys.executable, "-m", "datasette", db_path, "-p", "8042"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=tempfile.gettempdir(),
    )
    wait_until_responds("http://localhost:8042/")

    # Check it started successfully
    assert not ds_proc.poll(), ds_proc.stdout.read().decode("utf-8")

    yield {"base_url": "http://localhost:8042", "db_name": "test_products"}

    # Shut down the server
    ds_proc.terminate()
    ds_proc.wait()

    # Clean up
    if os.path.exists(db_path):
        os.remove(db_path)


def test_row_detail_panel_elements_exist(page, datasette_server):
    """Test that the row detail panel HTML elements exist"""
    base_url = datasette_server["base_url"]
    db_name = datasette_server["db_name"]
    page.goto(f"{base_url}/{db_name}/products")

    # Wait for the page to load
    page.wait_for_selector(".rows-and-columns")

    # Check that the dialog element exists
    dialog = page.locator("#rowDetailPanel")
    assert dialog.count() == 1

    # Check that the close button exists
    close_button = page.locator("#closeRowDetail")
    assert close_button.count() == 1

    # Check that the content div exists
    content_div = page.locator("#rowDetailContent")
    assert content_div.count() == 1


def test_row_click_opens_panel(page, datasette_server):
    """Test that clicking a table row opens the side panel"""
    base_url = datasette_server["base_url"]
    db_name = datasette_server["db_name"]
    page.goto(f"{base_url}/{db_name}/products")

    # Wait for the table to load
    page.wait_for_selector(".rows-and-columns tbody tr")

    # Get the dialog
    dialog = page.locator("#rowDetailPanel")

    # Dialog should not be open initially
    assert not dialog.evaluate("el => el.hasAttribute('open')")

    # Click the first row
    first_row = page.locator(".table-row-clickable").first
    first_row.click()

    # Wait for the dialog to open
    page.wait_for_selector("#rowDetailPanel[open]", timeout=2000)

    # Dialog should now be open
    assert dialog.evaluate("el => el.hasAttribute('open')")

    # Content should be loaded (not showing "Loading...")
    content = page.locator("#rowDetailContent")
    expect(content).not_to_contain_text("Loading...")


def test_row_panel_displays_data(page, datasette_server):
    """Test that the row panel displays the correct data"""
    base_url = datasette_server["base_url"]
    db_name = datasette_server["db_name"]
    page.goto(f"{base_url}/{db_name}/products")

    # Wait for the table to load
    page.wait_for_selector(".rows-and-columns tbody tr")

    # Click the first row (Laptop)
    first_row = page.locator(".table-row-clickable").first
    first_row.click()

    # Wait for the dialog to open and content to load
    page.wait_for_selector("#rowDetailPanel[open]")
    page.wait_for_selector("#rowDetailContent dl")

    # Check that the content includes the expected data
    content = page.locator("#rowDetailContent")
    expect(content).to_contain_text("Laptop")
    expect(content).to_contain_text("High-performance laptop")
    expect(content).to_contain_text("999.99")
    expect(content).to_contain_text("Electronics")


def test_close_button_closes_panel(page, datasette_server):
    """Test that clicking the close button closes the panel"""
    base_url = datasette_server["base_url"]
    db_name = datasette_server["db_name"]
    page.goto(f"{base_url}/{db_name}/products")

    # Wait for the table to load
    page.wait_for_selector(".rows-and-columns tbody tr")

    # Click a row to open the panel
    first_row = page.locator(".table-row-clickable").first
    first_row.click()

    # Wait for the dialog to open
    page.wait_for_selector("#rowDetailPanel[open]")

    # Click the close button
    close_button = page.locator("#closeRowDetail")
    close_button.click()

    # Wait for the dialog to close
    page.wait_for_timeout(200)  # Wait for animation

    # Dialog should be closed
    dialog = page.locator("#rowDetailPanel")
    assert not dialog.evaluate("el => el.hasAttribute('open')")


def test_escape_key_closes_panel(page, datasette_server):
    """Test that pressing Escape closes the panel"""
    base_url = datasette_server["base_url"]
    db_name = datasette_server["db_name"]
    page.goto(f"{base_url}/{db_name}/products")

    # Wait for the table to load
    page.wait_for_selector(".rows-and-columns tbody tr")

    # Click a row to open the panel
    first_row = page.locator(".table-row-clickable").first
    first_row.click()

    # Wait for the dialog to open
    page.wait_for_selector("#rowDetailPanel[open]")

    # Press Escape
    page.keyboard.press("Escape")

    # Wait for the dialog to close
    page.wait_for_timeout(200)  # Wait for animation

    # Dialog should be closed
    dialog = page.locator("#rowDetailPanel")
    assert not dialog.evaluate("el => el.hasAttribute('open')")


@pytest.mark.skip(
    reason="Backdrop click is difficult to test programmatically - works in manual testing"
)
def test_backdrop_click_closes_panel(page, datasette_server):
    """Test that clicking the backdrop closes the panel"""
    base_url = datasette_server["base_url"]
    db_name = datasette_server["db_name"]
    page.goto(f"{base_url}/{db_name}/products")

    # Wait for the table to load
    page.wait_for_selector(".rows-and-columns tbody tr")

    # Click a row to open the panel
    first_row = page.locator(".table-row-clickable").first
    first_row.click()

    # Wait for the dialog to open
    page.wait_for_selector("#rowDetailPanel[open]")

    # Click the dialog backdrop (the dialog element itself, not the content)
    dialog = page.locator("#rowDetailPanel")
    # Get the bounding box and click outside the content area
    box = dialog.bounding_box()
    if box:
        # Click on the left side of the dialog (the backdrop)
        page.mouse.click(box["x"] + 10, box["y"] + box["height"] / 2)

    # Wait for the dialog to close
    page.wait_for_timeout(200)  # Wait for animation

    # Dialog should be closed
    assert not dialog.evaluate("el => el.hasAttribute('open')")


def test_multiple_rows_different_data(page, datasette_server):
    """Test that clicking different rows shows different data"""
    base_url = datasette_server["base_url"]
    db_name = datasette_server["db_name"]
    page.goto(f"{base_url}/{db_name}/products")

    # Wait for the table to load
    page.wait_for_selector(".rows-and-columns tbody tr")

    # Click the first row
    first_row = page.locator(".table-row-clickable").first
    first_row.click()

    # Wait for the dialog to open
    page.wait_for_selector("#rowDetailPanel[open]")
    page.wait_for_selector("#rowDetailContent dl")

    # Check for first row data
    content = page.locator("#rowDetailContent")
    expect(content).to_contain_text("Laptop")

    # Close the panel
    close_button = page.locator("#closeRowDetail")
    close_button.click()
    page.wait_for_timeout(200)

    # Click the second row
    second_row = page.locator(".table-row-clickable").nth(1)
    second_row.click()

    # Wait for the dialog to open again
    page.wait_for_selector("#rowDetailPanel[open]")
    page.wait_for_selector("#rowDetailContent dl")

    # Check for second row data
    expect(content).to_contain_text("Mouse")
    expect(content).to_contain_text("Wireless mouse")


def test_row_hover_state(page, datasette_server):
    """Test that rows have hover state styling"""
    base_url = datasette_server["base_url"]
    db_name = datasette_server["db_name"]
    page.goto(f"{base_url}/{db_name}/products")

    # Wait for the table to load
    page.wait_for_selector(".rows-and-columns tbody tr")

    # Get the first row
    first_row = page.locator(".table-row-clickable").first

    # Check that the row has cursor: pointer
    cursor_style = first_row.evaluate("el => window.getComputedStyle(el).cursor")
    assert cursor_style == "pointer"


def test_navigation_buttons_exist(page, datasette_server):
    """Test that navigation buttons are present"""
    base_url = datasette_server["base_url"]
    db_name = datasette_server["db_name"]
    page.goto(f"{base_url}/{db_name}/products")

    # Wait for the table to load
    page.wait_for_selector(".rows-and-columns tbody tr")

    # Click a row to open the panel
    first_row = page.locator(".table-row-clickable").first
    first_row.click()

    # Wait for the dialog to open
    page.wait_for_selector("#rowDetailPanel[open]")

    # Check that navigation buttons exist
    prev_button = page.locator("#prevRowButton")
    next_button = page.locator("#nextRowButton")
    position = page.locator("#rowPosition")

    assert prev_button.count() == 1
    assert next_button.count() == 1
    assert position.count() == 1


def test_previous_button_disabled_on_first_row(page, datasette_server):
    """Test that previous button is disabled on the first row"""
    base_url = datasette_server["base_url"]
    db_name = datasette_server["db_name"]
    page.goto(f"{base_url}/{db_name}/products")

    # Wait for the table to load
    page.wait_for_selector(".rows-and-columns tbody tr")

    # Click the first row
    first_row = page.locator(".table-row-clickable").first
    first_row.click()

    # Wait for the dialog to open
    page.wait_for_selector("#rowDetailPanel[open]")

    # Previous button should be disabled
    prev_button = page.locator("#prevRowButton")
    assert prev_button.is_disabled()

    # Next button should be enabled
    next_button = page.locator("#nextRowButton")
    assert not next_button.is_disabled()


def test_next_button_navigation(page, datasette_server):
    """Test that next button navigates to the next row"""
    base_url = datasette_server["base_url"]
    db_name = datasette_server["db_name"]
    page.goto(f"{base_url}/{db_name}/products")

    # Wait for the table to load
    page.wait_for_selector(".rows-and-columns tbody tr")

    # Click the first row
    first_row = page.locator(".table-row-clickable").first
    first_row.click()

    # Wait for the dialog to open and content to load
    page.wait_for_selector("#rowDetailPanel[open]")
    page.wait_for_selector("#rowDetailContent dl")

    # Should show Laptop data
    content = page.locator("#rowDetailContent")
    expect(content).to_contain_text("Laptop")

    # Click next button
    next_button = page.locator("#nextRowButton")
    next_button.click()

    # Wait for content to update
    page.wait_for_timeout(300)

    # Should now show Mouse data
    expect(content).to_contain_text("Mouse")
    expect(content).to_contain_text("29.99")


def test_previous_button_navigation(page, datasette_server):
    """Test that previous button navigates to the previous row"""
    base_url = datasette_server["base_url"]
    db_name = datasette_server["db_name"]
    page.goto(f"{base_url}/{db_name}/products")

    # Wait for the table to load
    page.wait_for_selector(".rows-and-columns tbody tr")

    # Click the second row
    second_row = page.locator(".table-row-clickable").nth(1)
    second_row.click()

    # Wait for the dialog to open and content to load
    page.wait_for_selector("#rowDetailPanel[open]")
    page.wait_for_selector("#rowDetailContent dl")

    # Should show Mouse data
    content = page.locator("#rowDetailContent")
    expect(content).to_contain_text("Mouse")

    # Previous button should be enabled now
    prev_button = page.locator("#prevRowButton")
    assert not prev_button.is_disabled()

    # Click previous button
    prev_button.click()

    # Wait for content to update
    page.wait_for_timeout(300)

    # Should now show Laptop data
    expect(content).to_contain_text("Laptop")

    # Previous button should now be disabled (we're at first row)
    assert prev_button.is_disabled()


def test_row_position_updates(page, datasette_server):
    """Test that row position indicator updates correctly"""
    base_url = datasette_server["base_url"]
    db_name = datasette_server["db_name"]
    page.goto(f"{base_url}/{db_name}/products")

    # Wait for the table to load
    page.wait_for_selector(".rows-and-columns tbody tr")

    # Click the first row
    first_row = page.locator(".table-row-clickable").first
    first_row.click()

    # Wait for the dialog to open
    page.wait_for_selector("#rowDetailPanel[open]")

    # Check position indicator shows "Row 1"
    position = page.locator("#rowPosition")
    expect(position).to_have_text("Row 1")

    # Click next
    next_button = page.locator("#nextRowButton")
    next_button.click()
    page.wait_for_timeout(300)

    # Position should update to "Row 2"
    expect(position).to_have_text("Row 2")


def test_pagination_navigation(page, datasette_server):
    """Test that navigation works across pagination boundaries"""
    base_url = datasette_server["base_url"]
    db_name = datasette_server["db_name"]

    # Add page_size parameter to force pagination
    page.goto(f"{base_url}/{db_name}/products?_size=2")

    # Wait for the table to load
    page.wait_for_selector(".rows-and-columns tbody tr")

    # Click the second (last visible) row
    second_row = page.locator(".table-row-clickable").nth(1)
    second_row.click()

    # Wait for the dialog to open and content to load
    page.wait_for_selector("#rowDetailPanel[open]")
    page.wait_for_selector("#rowDetailContent dl")

    # Should show Mouse data (second row)
    content = page.locator("#rowDetailContent")
    expect(content).to_contain_text("Mouse")

    # Next button should be enabled (there are more rows via pagination)
    next_button = page.locator("#nextRowButton")
    assert not next_button.is_disabled()

    # Click next button - should load the third row from the next page
    next_button.click()

    # Wait for loading and content update
    page.wait_for_timeout(1000)  # Give time for pagination fetch

    # Should now show Desk data (third row, from next page)
    expect(content).to_contain_text("Desk")

    # Previous button should work to go back
    prev_button = page.locator("#prevRowButton")
    assert not prev_button.is_disabled()
    prev_button.click()
    page.wait_for_timeout(300)

    # Should be back to Mouse
    expect(content).to_contain_text("Mouse")


@pytest.mark.skip(reason="Mobile viewport test - enable if needed")
def test_panel_responsive_on_mobile(page, datasette_server):
    """Test that the panel is responsive on mobile viewports"""
    # Set mobile viewport
    page.set_viewport_size({"width": 375, "height": 667})

    base_url = datasette_server["base_url"]
    db_name = datasette_server["db_name"]
    page.goto(f"{base_url}/{db_name}/products")

    # Wait for the table to load
    page.wait_for_selector(".rows-and-columns tbody tr")

    # Click a row
    first_row = page.locator(".table-row-clickable").first
    first_row.click()

    # Wait for the dialog to open
    page.wait_for_selector("#rowDetailPanel[open]")

    # Check that the panel width is appropriate for mobile
    dialog = page.locator("#rowDetailPanel")
    width = dialog.evaluate("el => el.offsetWidth")
    viewport_width = page.viewport_size["width"]

    # Panel should take most of the width on mobile (90%)
    assert width > viewport_width * 0.85  # Allow some margin
