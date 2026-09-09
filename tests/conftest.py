"""共享匿名样本和券商单位配置。"""

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from stock_statement import platforms

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def baseline():
    """读取按核心流程手工推导的独立金额基线。"""
    return json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


@pytest.fixture
def anonymous_platforms(monkeypatch, baseline):
    """让匿名逆回购代码继续使用其来源平台的实际数量单位。"""
    configured = tuple(
        replace(platform, repo_units=tuple(
            (code, Decimal(unit))
            for code, unit in baseline[platform.name]["repo_units"].items()
        ))
        for platform in platforms.PLATFORMS
    )
    monkeypatch.setattr(platforms, "PLATFORMS", configured)
    return configured
