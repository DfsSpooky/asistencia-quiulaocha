from playwright.sync_api import Page, expect, sync_playwright
import time

def test_login_and_navigate(page: Page):
    # 1. Arrange: Go to login page
    page.goto("http://localhost:8000/login/")

    # Check if we are on login page
    expect(page).to_have_title("Iniciar Sesión - Quiulacocha")

    # 2. Act: Fill login form
    page.fill("input[name='username']", "admin")
    page.fill("input[name='password']", "admin")
    page.click("button[type='submit']")

    # 3. Assert: Check if redirected to list users (admin default)
    # The URL might be /lista_usuarios/ or similar
    expect(page).to_have_url("http://localhost:8000/lista_usuarios/")

    # 4. Act: Interact with user list HTMX search
    # Wait for the search input to be visible
    search_input = page.locator("input[name='query']")
    expect(search_input).to_be_visible()

    # Type something to trigger HTMX
    search_input.fill("Test")

    # Wait for HTMX to process (there's a 200ms delay in the attribute)
    time.sleep(1)

    # Take screenshot of user list
    page.screenshot(path="/home/jules/verification/user_list_htmx.png")

    # 5. Act: Navigate to Attendance History
    page.click("a[href='/historial-asistencias/']")

    # Assert we are on the page
    expect(page).to_have_title("Historial Operativo - Quiulacocha")

    # Take screenshot of attendance history
    page.screenshot(path="/home/jules/verification/attendance_history.png")

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            test_login_and_navigate(page)
            print("Frontend verification passed successfully!")
        except Exception as e:
            print(f"Frontend verification failed: {e}")
            page.screenshot(path="/home/jules/verification/error.png")
        finally:
            browser.close()
