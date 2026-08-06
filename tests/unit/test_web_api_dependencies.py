from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from time import sleep
from unittest.mock import patch

from src.web_api.dependencies import get_application_services


def test_application_services_are_built_once_under_concurrent_first_use() -> None:
    state = SimpleNamespace()
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    services = object()

    def build_services(*, data_dir: object, upload_policy: object = None) -> object:
        del data_dir
        del upload_policy
        sleep(0.02)
        return services

    with patch(
        "src.application.composition.build_application_services",
        side_effect=build_services,
    ) as build:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(
                executor.map(lambda _: get_application_services(request), range(32)),
            )

    assert all(result is services for result in results)
    build.assert_called_once()
