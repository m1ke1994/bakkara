"""Local browser/API workflow, isolated from the bookmaker and user's DB."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_ready(url, process):
    for _ in range(150):
        if process.poll() is not None:
            raise AssertionError("Тестовый сервер завершился до запуска")
        try:
            with urlopen(url, timeout=0.2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise AssertionError("Тестовый сервер не запустился")


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def test_calculator_browser_persistence_restart_and_errors(tmp_path):
    if not (ROOT / "frontend/node_modules/vite").is_dir():
        pytest.skip("Run npm ci in frontend to enable browser workflow tests")
    api_port, ui_port = free_port(), free_port()
    api_url, ui_url = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{ui_port}"
    env = {**os.environ, "OBSERVATION_DB": str(tmp_path / "isolated.sqlite3"),
           "FRONTEND_ORIGIN": ui_url, "TARGET_URL": "", "VITE_API_BASE_URL": api_url}
    processes = []
    with (tmp_path / "servers.log").open("w") as log:
        def launch(command, cwd, url):
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=log)
            processes.append(process)
            wait_ready(url, process)
            return process
        def backend():
            return launch([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(api_port)], ROOT / "backend", api_url + "/api/health")
        try:
            server = backend()
            launch(["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", str(ui_port)], ROOT / "frontend", ui_url)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context()
                context.route("**/*", lambda route: route.continue_() if route.request.url.startswith((api_url, ui_url)) else route.abort())
                page = context.new_page()
                page.goto(ui_url)
                planner = page.locator("#calculator")
                apply = planner.get_by_role("button", name="Применить настройки", exact=True)
                expect(apply).to_be_enabled()
                planner.get_by_label("Первоначальная ставка", exact=True).fill("171")
                planner.get_by_label("Множитель следующей ставки", exact=True).fill("1.84")
                planner.get_by_role("button", name="Рассчитать ряд").click()
                for index, value in enumerate([171, 314, 577, 1061, 1952, 3591, 6607, 12156], 1):
                    expect(planner.get_by_label(f"Шаг {index}", exact=True)).to_have_value(str(value))
                planner.get_by_label("Шаг 2", exact=True).fill("314.29")
                apply.click()
                expect(planner.get_by_role("button", name="Настройки сохранены", exact=True)).to_be_visible()
                with urlopen(api_url + "/api/stake-plan") as response:
                    assert json.load(response)["row"][1] == "314.29"
                page.reload()
                expect(planner.get_by_label("Шаг 2", exact=True)).to_have_value("314.29")
                stop(server)
                backend()
                page.reload()
                expect(planner.get_by_label("Шаг 2", exact=True)).to_have_value("314.29")

                other = context.new_page()
                other.goto(ui_url)
                other_planner = other.locator("#calculator")
                expect(other_planner.get_by_label("Шаг 2", exact=True)).to_have_value("314.29")
                planner.get_by_label("Шаг 2", exact=True).fill("315.29")
                apply.click()
                expect(planner.get_by_role("button", name="Настройки сохранены", exact=True)).to_be_visible()
                other_planner.get_by_label("Шаг 2", exact=True).fill("316.29")
                other_planner.get_by_role("button", name="Применить настройки", exact=True).click()
                expect(other_planner.get_by_role("alert")).to_contain_text("другом окне")
                expect(other_planner.get_by_label("Шаг 2", exact=True)).to_have_value("316.29")
                other_planner.get_by_role("button", name="Загрузить сохранённый ряд").click()
                expect(other_planner.get_by_label("Шаг 2", exact=True)).to_have_value("315.29")
                other.close()

                page.route("**/api/stake-plan", lambda route: route.fulfill(status=503, content_type="application/json", headers={"access-control-allow-origin": ui_url}, body=json.dumps({"detail": "Сохранение временно недоступно"})))
                planner.get_by_label("Шаг 2", exact=True).fill("317.29")
                apply.click()
                expect(planner.get_by_role("alert")).to_contain_text("временно недоступно")
                expect(planner.get_by_label("Шаг 2", exact=True)).to_have_value("317.29")
                page.reload()
                expect(planner.get_by_role("alert")).to_be_visible()
                expect(planner.get_by_label("Шаг 2", exact=True)).to_be_disabled()
                page.unroute("**/api/stake-plan")
                planner.get_by_role("button", name="Загрузить сохранённый ряд").click()
                expect(planner.get_by_label("Шаг 2", exact=True)).to_have_value("315.29")
                expect(planner.get_by_label("Шаг 2", exact=True)).to_be_enabled()
                browser.close()
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    stop(process)
