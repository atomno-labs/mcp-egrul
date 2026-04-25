"""Smoke-тесты Pydantic-схем публичного контракта."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from mcp_egrul.schemas import (
    AddressStructured,
    BulkItemError,
    BulkResult,
    CompanyCard,
    Director,
    Founder,
    IECard,
    OkvedEntry,
    SearchHit,
)


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


class TestOkvedEntry:
    def test_valid_with_description(self) -> None:
        o = OkvedEntry(code="64.19", description="Денежное посредничество прочее")
        assert o.code == "64.19"

    def test_valid_without_description(self) -> None:
        o = OkvedEntry(code="64.19")
        assert o.description is None


class TestCompanyCard:
    def test_minimum_valid(self) -> None:
        c = CompanyCard(
            inn="7707083893",
            ogrn="1027700132195",
            name_short="ПАО СБЕРБАНК",
            name_full="Публичное акционерное общество 'Сбербанк России'",
            status="active",
            registered_at=date(1991, 3, 20),
            source="opendata",
            source_date=date(2026, 4, 1),
            fetched_at=_now_utc(),
        )
        assert c.inn == "7707083893"
        assert c.okved_additional == []
        assert c.founders == []
        assert c.director is None

    def test_with_director_and_founders(self) -> None:
        c = CompanyCard(
            inn="7707083893",
            ogrn="1027700132195",
            name_short="ПАО СБЕРБАНК",
            name_full="Публичное акционерное общество 'Сбербанк России'",
            status="active",
            registered_at=date(1991, 3, 20),
            director=Director(fio="Греф Г. О.", position="Президент"),
            founders=[
                Founder(
                    type="legal",
                    name="Центробанк РФ",
                    inn="7702235133",
                    share_percent=50.0,
                )
            ],
            source="opendata",
            source_date=date(2026, 4, 1),
            fetched_at=_now_utc(),
        )
        assert c.director is not None
        assert c.founders[0].share_percent == 50.0

    def test_rejects_unknown_status(self) -> None:
        with pytest.raises(PydanticValidationError):
            CompanyCard(
                inn="7707083893",
                ogrn="1027700132195",
                name_short="ПАО СБЕРБАНК",
                name_full="Full",
                status="zombie",
                registered_at=date(2000, 1, 1),
                source="opendata",
                source_date=date(2026, 4, 1),
                fetched_at=_now_utc(),
            )

    def test_rejects_unknown_source(self) -> None:
        with pytest.raises(PydanticValidationError):
            CompanyCard(
                inn="7707083893",
                ogrn="1027700132195",
                name_short="Short",
                name_full="Full",
                status="active",
                registered_at=date(2000, 1, 1),
                source="made_up_source",
                source_date=date(2026, 4, 1),
                fetched_at=_now_utc(),
            )

    def test_rejects_share_over_100(self) -> None:
        with pytest.raises(PydanticValidationError):
            Founder(type="legal", name="X", share_percent=150.0)


class TestIECard:
    def test_minimum_valid(self) -> None:
        ie = IECard(
            ogrnip="304500116000061",
            inn="500100732259",
            fio="Иванов И.И.",
            status="active",
            registered_at=date(2004, 1, 15),
            source="opendata",
            source_date=date(2026, 4, 1),
            fetched_at=_now_utc(),
        )
        assert ie.status == "active"

    def test_rejects_unknown_ie_status(self) -> None:
        with pytest.raises(PydanticValidationError):
            IECard(
                ogrnip="304500116000061",
                inn="500100732259",
                fio="Иванов И.И.",
                status="active-but-weird",
                registered_at=date(2004, 1, 15),
                source="opendata",
                source_date=date(2026, 4, 1),
                fetched_at=_now_utc(),
            )


class TestSearchHit:
    def test_valid_company(self) -> None:
        hit = SearchHit(
            kind="company",
            inn="7707083893",
            ogrn="1027700132195",
            name="ПАО СБЕРБАНК",
            status="active",
            relevance_score=0.85,
        )
        assert 0.0 <= hit.relevance_score <= 1.0

    def test_relevance_must_be_bounded(self) -> None:
        with pytest.raises(PydanticValidationError):
            SearchHit(
                kind="company",
                inn="7707083893",
                ogrn="1027700132195",
                name="Short",
                status="active",
                relevance_score=2.5,
            )

    def test_rejects_unknown_kind(self) -> None:
        with pytest.raises(PydanticValidationError):
            SearchHit(
                kind="martian",
                inn="7707083893",
                ogrn="1027700132195",
                name="Short",
                status="active",
                relevance_score=0.5,
            )


class TestBulkResult:
    def test_empty(self) -> None:
        r = BulkResult(cards=[], errors=[], requested=0, found=0)
        assert r.requested == 0

    def test_partial_with_errors(self) -> None:
        r = BulkResult(
            cards=[],
            errors=[
                BulkItemError(inn="bad", code="invalid_input", message="msg"),
            ],
            requested=1,
            found=0,
        )
        assert r.errors[0].code == "invalid_input"


class TestAddressStructured:
    def test_valid(self) -> None:
        a = AddressStructured(
            postal_code="117997",
            region="г. Москва",
            street="ул. Вавилова",
            house="19",
        )
        assert a.postal_code == "117997"
        assert a.city is None
