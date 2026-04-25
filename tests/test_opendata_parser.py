"""Юнит-тесты XML-парсера дампов ФНС.

Покрытие:
    * парсинг канонического ЕГРЮЛ-фрагмента (2 валидные компании + 1 skip),
    * парсинг канонического ЕГРИП-фрагмента (1 active + 1 closed),
    * ZIP-архив с XML,
    * невалидный путь (файл не существует, папка, неизвестное расширение),
    * невалидный registry.
"""

from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path

import pytest

from mcp_egrul.errors import McpEgrulError
from mcp_egrul.sources.opendata_parser import (
    ParseStats,
    iter_dump_records,
)

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_DATE = date(2026, 4, 1)


def test_parse_egrul_sample_yields_two_valid_and_one_skip() -> None:
    stats = ParseStats()
    records = list(
        iter_dump_records(
            FIXTURES / "egrul_sample.xml",
            registry="egrul",
            source_date=SAMPLE_DATE,
            stats=stats,
        )
    )
    assert stats.records_parsed == 3
    assert stats.records_yielded == 2
    assert len(records) == 2
    assert len(stats.errors) == 1
    assert "Неизвестный статус" in stats.errors[0].message

    sberbank = next(r for r in records if r["inn"] == "7707083893")
    assert sberbank["ogrn"] == "1027700132195"
    assert sberbank["kpp"] == "773601001"
    assert sberbank["name_short"] == "ПАО СБЕРБАНК"
    assert "СБЕРБАНК" in sberbank["name_full"]
    assert sberbank["status"] == "active"
    assert sberbank["registered_at"] == "1991-03-20"
    assert sberbank["okved_main_code"] == "64.19"
    assert sberbank["authorized_capital"] == 67760844000.0
    assert sberbank["source"] == "opendata"
    assert sberbank["source_date"] == "2026-04-01"

    data_json = sberbank["data_json"]
    assert data_json["okved_additional"] == [
        {"code": "64.99.1", "description": "Вложения в ценные бумаги"}
    ]
    assert data_json["director"]["fio"] == "Греф Герман Оскарович"
    assert data_json["director"]["position"].startswith("Президент")
    assert data_json["director"]["inn"] == "773601010101"

    founders = data_json["founders"]
    assert len(founders) == 1
    assert founders[0]["type"] == "legal"
    assert founders[0]["name"].startswith("Центральный банк")
    assert founders[0]["share_percent"] == 50.0

    gazprom = next(r for r in records if r["inn"] == "7728168971")
    assert gazprom["ogrn"] == "1037700013020"
    assert gazprom["kpp"] == "997250001"
    assert gazprom["status"] == "active"
    assert "Почтамтская" in gazprom["address_legal"]


def test_parse_egrip_sample_yields_active_and_closed() -> None:
    stats = ParseStats()
    records = list(
        iter_dump_records(
            FIXTURES / "egrip_sample.xml",
            registry="egrip",
            source_date=SAMPLE_DATE,
            stats=stats,
        )
    )
    assert stats.records_parsed == 2
    assert stats.records_yielded == 2
    assert len(stats.errors) == 0

    ivanov = next(r for r in records if r["ogrnip"] == "304500116000061")
    assert ivanov["inn"] == "500100732259"
    assert ivanov["fio"] == "Иванов Иван Иванович"
    assert ivanov["citizenship"] == "RU"
    assert ivanov["status"] == "active"
    assert ivanov["registered_at"] == "2004-01-15"
    assert ivanov["closed_at"] is None
    assert ivanov["okved_main_code"] == "47.91.2"
    assert ivanov["data_json"]["okved_additional"] == [
        {"code": "47.99", "description": "Торговля розничная прочая вне магазинов"}
    ]

    petrova = next(r for r in records if r["ogrnip"] == "320774000000048")
    assert petrova["inn"] == "773173381311"
    assert petrova["status"] == "closed"
    assert petrova["closed_at"] == "2024-09-12"


def test_parse_zip_archive_with_xml(tmp_path: Path) -> None:
    zip_path = tmp_path / "EGRUL_FRAG.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(FIXTURES / "egrul_sample.xml", arcname="egrul_01.xml")
        zf.write(FIXTURES / "egrul_sample.xml", arcname="egrul_02.xml")

    stats = ParseStats()
    records = list(
        iter_dump_records(
            zip_path,
            registry="egrul",
            source_date=SAMPLE_DATE,
            stats=stats,
        )
    )
    assert stats.records_parsed == 6
    assert stats.records_yielded == 4
    assert len(records) == 4
    assert len(stats.errors) == 2


def test_parse_missing_file_raises(tmp_path: Path) -> None:
    stats = ParseStats()
    with pytest.raises(McpEgrulError, match="Файл дампа не найден"):
        list(
            iter_dump_records(
                tmp_path / "does-not-exist.xml",
                registry="egrul",
                source_date=SAMPLE_DATE,
                stats=stats,
            )
        )


def test_parse_directory_raises(tmp_path: Path) -> None:
    stats = ParseStats()
    with pytest.raises(McpEgrulError, match="Ожидается файл"):
        list(
            iter_dump_records(
                tmp_path,
                registry="egrul",
                source_date=SAMPLE_DATE,
                stats=stats,
            )
        )


def test_parse_unknown_extension_raises(tmp_path: Path) -> None:
    garbage = tmp_path / "file.dat"
    garbage.write_bytes(b"\x00\x01")
    stats = ParseStats()
    with pytest.raises(McpEgrulError, match="Неподдерживаемое расширение"):
        list(
            iter_dump_records(
                garbage,
                registry="egrul",
                source_date=SAMPLE_DATE,
                stats=stats,
            )
        )


def test_parse_zip_without_xml_raises(tmp_path: Path) -> None:
    zip_path = tmp_path / "empty.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("readme.txt", "no xml inside")
    stats = ParseStats()
    with pytest.raises(McpEgrulError, match="нет .xml файлов"):
        list(
            iter_dump_records(
                zip_path,
                registry="egrul",
                source_date=SAMPLE_DATE,
                stats=stats,
            )
        )


def test_parse_unknown_registry_raises() -> None:
    stats = ParseStats()
    with pytest.raises(McpEgrulError, match="Неизвестный реестр"):
        list(
            iter_dump_records(
                FIXTURES / "egrul_sample.xml",
                registry="bogus",
                source_date=SAMPLE_DATE,
                stats=stats,
            )
        )
