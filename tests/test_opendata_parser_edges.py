"""Дополнительные тесты edge-case-веток XML-парсера ФНС (`opendata_parser`).

Разделены с `test_opendata_parser.py`, чтобы не раздувать основной тест-файл:
там — happy-path с полноценными fixture-XML, здесь — точечные тесты
приватных хелперов и отдельных веток через синтетические `etree._Element`.

Покрываемые ранее непротестированные участки:
    * `_parse_company` — отбрасывание записи с некорректным ИНН/ОГРН/КПП;
    * `_parse_ie` — отбрасывание записи с некорректным ОГРНИП/ИНН ФЛ;
    * `_require_attr` — отсутствующий обязательный атрибут;
    * `_parse_iso_date_attr` — пустая обязательная / невалидный формат;
    * `_parse_company_names` — старый формат (`НаимЮЛПолн` на `<СвЮЛ>`);
    * `_parse_company_status` — fallback на `ДатаПрекрЮЛ` / active при отсутствии
      `<СвСтатус>`, неизвестный статус → `_RecordSkipped`;
    * `_parse_address_raw` — нет `<АдресЮЛ>`, `Почтовый` вместо `Индекс`,
      fallback на `АдрЮЛФИАС`;
    * `_parse_okved_block` — нет `<СвОКВЭД>`, только основной;
    * `_parse_authorized_capital` — нет элемента, пустое значение, невалидное;
    * `_parse_director` / `_parse_founders` — нет руководителя, учредитель-ЮЛ,
      учредитель-ФЛ, неизвестный тег учредителя, пустые ФИО;
    * `_parse_share` — нет `<ДоляУстКап>`, невалидный percent/sum, > 100, < 0;
    * `_parse_fio_element`, `_extract_inn` — edge cases;
    * `_parse_ie_fio` / `_parse_ie_citizenship` / `_parse_ie_status`
      — иностранное гражданство, старый флаг `<СвПрекрИП>`, неизвестный статус ИП.
"""

from __future__ import annotations

from datetime import date

import pytest
from lxml import etree

from mcp_egrul.sources.opendata_parser import (
    _COMPANY_STATUS_MAP,
    _DATA_SOURCE_OPENDATA,
    _IE_STATUS_MAP,
    _extract_inn,
    _parse_address_raw,
    _parse_authorized_capital,
    _parse_company,
    _parse_company_names,
    _parse_company_status,
    _parse_director,
    _parse_fio_element,
    _parse_founders,
    _parse_ie,
    _parse_ie_citizenship,
    _parse_ie_close_date,
    _parse_ie_fio,
    _parse_ie_status,
    _parse_iso_date_attr,
    _parse_okved_block,
    _parse_share,
    _RecordSkipped,
    _require_attr,
)


def _xml(s: str) -> etree._Element:
    """Построить `etree._Element` из строки (без `<?xml ...?>`-пролога)."""
    return etree.fromstring(s.encode("utf-8"))


# ---------------------------------------------------------------------------
# _parse_company — отбрасывание по некорректным идентификаторам.
# ---------------------------------------------------------------------------


def test_parse_company_skips_invalid_inn_length() -> None:
    el = _xml(
        '<СвЮЛ ОГРН="1027700132195" ИНН="1234567890" КПП="773601001" ДатаОГРН="20.03.1991">'
        '<СвНаимЮЛ НаимЮЛПолн="ООО Тест"/>'
        '<СвСтатус НаимСтатусЮЛ="Действующее"/>'
        "</СвЮЛ>"
    )
    with pytest.raises(_RecordSkipped, match="контрольной цифры"):
        _parse_company(el)


def test_parse_company_skips_invalid_ogrn_length() -> None:
    el = _xml(
        '<СвЮЛ ОГРН="102770013219" ИНН="7707083893" КПП="773601001" ДатаОГРН="20.03.1991">'
        '<СвНаимЮЛ НаимЮЛПолн="ООО Тест"/>'
        '<СвСтатус НаимСтатусЮЛ="Действующее"/>'
        "</СвЮЛ>"
    )
    with pytest.raises(_RecordSkipped, match="ОГРН"):
        _parse_company(el)


def test_parse_company_skips_invalid_kpp_length() -> None:
    el = _xml(
        '<СвЮЛ ОГРН="1027700132195" ИНН="7707083893" КПП="12345" ДатаОГРН="20.03.1991">'
        '<СвНаимЮЛ НаимЮЛПолн="ООО Тест"/>'
        '<СвСтатус НаимСтатусЮЛ="Действующее"/>'
        "</СвЮЛ>"
    )
    with pytest.raises(_RecordSkipped, match="КПП"):
        _parse_company(el)


def test_parse_company_accepts_record_without_kpp() -> None:
    """`КПП` — необязательный атрибут, запись должна проходить без него."""
    el = _xml(
        '<СвЮЛ ОГРН="1027700132195" ИНН="7707083893" ДатаОГРН="20.03.1991">'
        '<СвНаимЮЛ НаимЮЛПолн="ООО Тест"/>'
        '<СвСтатус НаимСтатусЮЛ="Действующее"/>'
        "</СвЮЛ>"
    )
    parsed = _parse_company(el)
    assert parsed["kpp"] is None
    assert parsed["inn"] == "7707083893"


# ---------------------------------------------------------------------------
# _parse_ie — отбрасывание по некорректным идентификаторам.
# ---------------------------------------------------------------------------


def test_parse_ie_skips_invalid_ogrnip_length() -> None:
    el = _xml(
        '<СвИП ОГРНИП="30450011600006" ИННФЛ="500100732259" ДатаОГРНИП="15.01.2004">'
        '<СвФЛ ФамилияРус="Иванов" ИмяРус="Иван"/>'
        "</СвИП>"
    )
    with pytest.raises(_RecordSkipped, match="ОГРНИП"):
        _parse_ie(el)


def test_parse_ie_skips_invalid_inn_length() -> None:
    el = _xml(
        '<СвИП ОГРНИП="304500116000061" ИННФЛ="50010073225" ДатаОГРНИП="15.01.2004">'
        '<СвФЛ ФамилияРус="Иванов" ИмяРус="Иван"/>'
        "</СвИП>"
    )
    with pytest.raises(_RecordSkipped, match="ИНН ИП"):
        _parse_ie(el)


# ---------------------------------------------------------------------------
# _require_attr / _parse_iso_date_attr.
# ---------------------------------------------------------------------------


def test_require_attr_raises_on_missing() -> None:
    el = _xml("<Dummy/>")
    with pytest.raises(_RecordSkipped, match="обязательный атрибут"):
        _require_attr(el, "ИНН", record_id=None)


def test_require_attr_raises_on_empty_value() -> None:
    el = _xml('<Dummy ИНН=""/>')
    with pytest.raises(_RecordSkipped, match="обязательный атрибут"):
        _require_attr(el, "ИНН", record_id=None)


def test_require_attr_returns_value_when_present() -> None:
    el = _xml('<Dummy ИНН="7707083893"/>')
    assert _require_attr(el, "ИНН", record_id=None) == "7707083893"


def test_parse_iso_date_attr_required_but_missing_raises() -> None:
    el = _xml("<Dummy/>")
    with pytest.raises(_RecordSkipped, match="обязательное поле"):
        _parse_iso_date_attr(el, "ДатаОГРН", required=True, record_id="x")


def test_parse_iso_date_attr_required_but_empty_raises() -> None:
    el = _xml('<Dummy ДатаОГРН=""/>')
    with pytest.raises(_RecordSkipped, match="обязательное поле"):
        _parse_iso_date_attr(el, "ДатаОГРН", required=True, record_id="x")


def test_parse_iso_date_attr_invalid_format_raises() -> None:
    el = _xml('<Dummy ДатаОГРН="2026-04-24"/>')
    with pytest.raises(_RecordSkipped, match="формат даты"):
        _parse_iso_date_attr(el, "ДатаОГРН", required=True, record_id="x")


def test_parse_iso_date_attr_optional_absent_returns_none() -> None:
    el = _xml("<Dummy/>")
    assert (
        _parse_iso_date_attr(el, "ДатаПрекрЮЛ", required=False, record_id="x") is None
    )


def test_parse_iso_date_attr_happy_path() -> None:
    el = _xml('<Dummy ДатаОГРН="20.03.1991"/>')
    assert _parse_iso_date_attr(el, "ДатаОГРН", required=True, record_id="x") == "1991-03-20"


# ---------------------------------------------------------------------------
# _parse_company_names — старые форматы.
# ---------------------------------------------------------------------------


def test_parse_company_names_reads_legacy_root_attributes() -> None:
    """Fallback: `НаимЮЛПолн` / `НаимЮЛСокр` как атрибуты на самом `<СвЮЛ>`."""
    el = _xml('<СвЮЛ НаимЮЛПолн="ООО Легаси" НаимЮЛСокр="ООО Л"/>')
    full, short = _parse_company_names(el, record_id="x")
    assert full == "ООО Легаси"
    assert short == "ООО Л"


def test_parse_company_names_missing_full_name_raises() -> None:
    el = _xml('<СвЮЛ><СвНаимЮЛ НаимСокр="ЗАГ"/></СвЮЛ>')
    with pytest.raises(_RecordSkipped, match="полное наименование"):
        _parse_company_names(el, record_id="x")


def test_parse_company_names_modern_with_short_name() -> None:
    el = _xml(
        '<СвЮЛ>'
        '<СвНаимЮЛ НаимЮЛПолн="ПАО СБЕРБАНК РОССИИ">'
        '<СвНаимЮЛСокр НаимСокр="ПАО СБЕРБАНК"/>'
        '</СвНаимЮЛ>'
        '</СвЮЛ>'
    )
    full, short = _parse_company_names(el, record_id="x")
    assert full == "ПАО СБЕРБАНК РОССИИ"
    assert short == "ПАО СБЕРБАНК"


# ---------------------------------------------------------------------------
# _parse_company_status — fallback-ветки.
# ---------------------------------------------------------------------------


def test_company_status_map_contains_canonical_slugs() -> None:
    """Регрессионный: маппинг никогда не должен терять active/liquidated."""
    assert _COMPANY_STATUS_MAP["действующее"] == "active"
    assert _COMPANY_STATUS_MAP["ликвидировано"] == "liquidated"


def test_parse_company_status_without_svstatus_uses_liquidation_flag() -> None:
    el = _xml('<СвЮЛ ДатаПрекрЮЛ="01.01.2020"/>')
    assert _parse_company_status(el, record_id="x") == "liquidated"


def test_parse_company_status_without_svstatus_defaults_to_active() -> None:
    el = _xml("<СвЮЛ/>")
    assert _parse_company_status(el, record_id="x") == "active"


def test_parse_company_status_legacy_status_yul_attribute() -> None:
    el = _xml('<СвЮЛ><СвСтатус СтатусЮЛ="Действующее"/></СвЮЛ>')
    assert _parse_company_status(el, record_id="x") == "active"


def test_parse_company_status_unknown_raises() -> None:
    el = _xml('<СвЮЛ><СвСтатус НаимСтатусЮЛ="Марсианский"/></СвЮЛ>')
    with pytest.raises(_RecordSkipped, match="Неизвестный статус"):
        _parse_company_status(el, record_id="x")


# ---------------------------------------------------------------------------
# _parse_address_raw — разнообразие форматов.
# ---------------------------------------------------------------------------


def test_parse_address_raw_returns_none_when_no_address() -> None:
    el = _xml("<СвЮЛ/>")
    assert _parse_address_raw(el) is None


def test_parse_address_raw_uses_postal_fallback_attribute() -> None:
    el = _xml(
        '<СвЮЛ>'
        '<АдресЮЛ Почтовый="123456">'
        '<Город Наименование="Тестоград"/>'
        '</АдресЮЛ>'
        '</СвЮЛ>'
    )
    result = _parse_address_raw(el)
    assert result is not None
    assert result.startswith("123456")
    assert "Тестоград" in result


def test_parse_address_raw_picks_up_apartment() -> None:
    el = _xml(
        '<СвЮЛ>'
        '<АдресЮЛ Дом="10" Кварт="5">'
        '<Улица Тип="ул" Наим="Ленина"/>'
        '</АдресЮЛ>'
        '</СвЮЛ>'
    )
    result = _parse_address_raw(el)
    assert result is not None
    assert "ул Ленина" in result
    assert "д. 10" in result
    assert "кв. 5" in result


def test_parse_address_raw_falls_back_to_fias_when_no_parts() -> None:
    el = _xml(
        '<СвЮЛ>'
        '<АдресЮЛ АдрЮЛФИАС="some-fias-guid-or-text-address"/>'
        '</СвЮЛ>'
    )
    assert _parse_address_raw(el) == "some-fias-guid-or-text-address"


def test_parse_address_raw_falls_back_to_plain_adres_when_no_parts() -> None:
    el = _xml('<СвЮЛ><АдресЮЛ Адрес="Москва, где-то"/></СвЮЛ>')
    assert _parse_address_raw(el) == "Москва, где-то"


def test_parse_address_raw_handles_text_content_without_attributes() -> None:
    el = _xml(
        "<СвЮЛ>"
        "<АдресЮЛ>"
        "<Город>Самара</Город>"
        "</АдресЮЛ>"
        "</СвЮЛ>"
    )
    result = _parse_address_raw(el)
    assert result == "Самара"


def test_parse_address_raw_skips_empty_child_tag_without_name() -> None:
    """Пустой `<НаимРегион/>` без атрибутов и без текста:
    ветка `elif name:` False → пропускаем, но соседние теги с данными добираем.

    Закрывает ветку 507->496 в opendata_parser.py — `name` is None,
    поэтому ни if-, ни elif-блок не добавляет в parts.
    """
    el = _xml(
        "<СвЮЛ>"
        "<АдресЮЛ>"
        "<НаимРегион/>"
        "<Город>Казань</Город>"
        "</АдресЮЛ>"
        "</СвЮЛ>"
    )
    result = _parse_address_raw(el)
    assert result == "Казань"


def test_parse_address_raw_skips_child_with_only_whitespace_text() -> None:
    """`<Улица>   </Улица>` — text после strip() пустой → name=None → skip."""
    el = _xml(
        "<СвЮЛ>"
        "<АдресЮЛ>"
        "<Улица>   </Улица>"
        "<Город>Уфа</Город>"
        "</АдресЮЛ>"
        "</СвЮЛ>"
    )
    result = _parse_address_raw(el)
    assert result == "Уфа"


def test_iter_records_from_stream_handles_target_tag_at_root_level() -> None:
    """Редкий кейс: корневой элемент XML-дампа сам совпадает с target_tag
    (например, одиночная записка, а не полный дамп). Тогда `element.getparent()`
    возвращает None, и блок cleanup пропускается без падения.

    Закрывает ветку 255 в opendata_parser.py: `if parent is not None:` = False.
    """
    import io

    from mcp_egrul.sources import opendata_parser as parser_module

    xml_bytes = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<СвЮЛ ОГРН="1027700132195" ИНН="7707083893" КПП="773601001" '
        'ДатаОГРН="20.03.1991">'
        '<СвНаимЮЛ НаимЮЛПолн="ТЕСТОВОЕ ЮЛ">'
        '<СвНаимЮЛСокр НаимСокр="ТЕСТ"/>'
        "</СвНаимЮЛ>"
        '<СвСтатус НаимСтатусЮЛ="Действующее"/>'
        "</СвЮЛ>"
    ).encode()

    stats = parser_module.ParseStats()
    stream = io.BytesIO(xml_bytes)
    records = list(
        parser_module._iter_records_from_stream(
            stream,
            target_tag=parser_module._TAG_COMPANY,
            parse_fn=parser_module._parse_company,
            registry="egrul",
            source_date=date(2026, 4, 1),
            stats=stats,
        )
    )
    assert len(records) == 1
    assert records[0]["inn"] == "7707083893"
    assert stats.records_parsed == 1


# ---------------------------------------------------------------------------
# _parse_okved_block.
# ---------------------------------------------------------------------------


def test_parse_okved_block_empty_when_no_svokved() -> None:
    el = _xml("<СвЮЛ/>")
    main, additional = _parse_okved_block(el)
    assert main is None
    assert additional == []


def test_parse_okved_block_only_main() -> None:
    el = _xml(
        '<СвЮЛ>'
        '<СвОКВЭД>'
        '<СвОКВЭДОсн КодОКВЭД="64.19" НаимОКВЭД="Денежное посредничество"/>'
        '</СвОКВЭД>'
        '</СвЮЛ>'
    )
    main, additional = _parse_okved_block(el)
    assert main == {"code": "64.19", "description": "Денежное посредничество"}
    assert additional == []


def test_parse_okved_block_without_main_but_with_additional() -> None:
    el = _xml(
        '<СвЮЛ>'
        '<СвОКВЭД>'
        '<СвОКВЭДДоп КодОКВЭД="47.99" НаимОКВЭД="Прочая торговля"/>'
        '</СвОКВЭД>'
        '</СвЮЛ>'
    )
    main, additional = _parse_okved_block(el)
    assert main is None
    assert additional == [{"code": "47.99", "description": "Прочая торговля"}]


# ---------------------------------------------------------------------------
# _parse_authorized_capital.
# ---------------------------------------------------------------------------


def test_parse_authorized_capital_returns_none_when_element_missing() -> None:
    el = _xml("<СвЮЛ/>")
    assert _parse_authorized_capital(el) is None


def test_parse_authorized_capital_empty_value_returns_none() -> None:
    el = _xml('<СвЮЛ><СвУстКап СумКап=""/></СвЮЛ>')
    assert _parse_authorized_capital(el) is None


def test_parse_authorized_capital_legacy_element_and_attr() -> None:
    el = _xml('<СвЮЛ><СвКапитал СумУстКап="10000.50"/></СвЮЛ>')
    assert _parse_authorized_capital(el) == 10000.50


def test_parse_authorized_capital_invalid_value_raises() -> None:
    el = _xml('<СвЮЛ><СвУстКап СумКап="not-a-number"/></СвЮЛ>')
    with pytest.raises(_RecordSkipped, match="уставного капитала"):
        _parse_authorized_capital(el)


# ---------------------------------------------------------------------------
# _parse_director.
# ---------------------------------------------------------------------------


def test_parse_director_returns_none_when_no_ruk() -> None:
    el = _xml("<СвЮЛ/>")
    assert _parse_director(el) is None


def test_parse_director_default_position_when_missing() -> None:
    el = _xml(
        "<СвЮЛ>"
        "<СвРуковод>"
        '<СвФЛ ФамилияРус="Петров" ИмяРус="Пётр" ИННФЛ="500100732259"/>'
        "</СвРуковод>"
        "</СвЮЛ>"
    )
    director = _parse_director(el)
    assert director is not None
    assert director["fio"] == "Петров Пётр"
    assert director["position"] == "Руководитель"
    assert director["inn"] == "500100732259"


def test_parse_director_returns_none_when_fio_empty() -> None:
    """Если СвФЛ пустое — директора считать не распарсенным."""
    el = _xml("<СвЮЛ><СвРуковод><СвФЛ/></СвРуковод></СвЮЛ>")
    assert _parse_director(el) is None


def test_parse_director_with_position_element() -> None:
    el = _xml(
        "<СвЮЛ>"
        "<СвРуковод>"
        '<СвФЛ ФамилияРус="Иванов" ИмяРус="И" ОтчествоРус="И"/>'
        '<СвДолжн НаимДолжн="Генеральный директор"/>'
        "</СвРуковод>"
        "</СвЮЛ>"
    )
    director = _parse_director(el)
    assert director is not None
    assert director["position"] == "Генеральный директор"


def test_parse_director_position_element_without_name_falls_back_to_default() -> None:
    el = _xml(
        "<СвЮЛ>"
        "<СвРуковод>"
        '<СвФЛ ФамилияРус="Иванов" ИмяРус="И"/>'
        "<СвДолжн/>"
        "</СвРуковод>"
        "</СвЮЛ>"
    )
    director = _parse_director(el)
    assert director is not None
    assert director["position"] == "Руководитель"


def test_parse_director_reads_fio_directly_from_ruk_when_no_svfl() -> None:
    el = _xml(
        '<СвЮЛ>'
        '<СвРуковод ФамилияРус="Прямой" ИмяРус="П" ОтчествоРус="П"/>'
        '</СвЮЛ>'
    )
    director = _parse_director(el)
    assert director is not None
    assert director["fio"] == "Прямой П П"


# ---------------------------------------------------------------------------
# _parse_founders + _parse_share.
# ---------------------------------------------------------------------------


def test_parse_founders_returns_empty_when_no_container() -> None:
    el = _xml("<СвЮЛ/>")
    assert _parse_founders(el) == []


def test_parse_founders_skips_unknown_tag() -> None:
    el = _xml(
        "<СвЮЛ>"
        "<СведУчредит>"
        '<УчрНеизвест НаимЮЛ="Икс"/>'
        "</СведУчредит>"
        "</СвЮЛ>"
    )
    assert _parse_founders(el) == []


def test_parse_founders_skips_founder_without_name() -> None:
    el = _xml("<СвЮЛ><СведУчредит><УчрЮЛРос/></СведУчредит></СвЮЛ>")
    assert _parse_founders(el) == []


def test_parse_founders_legacy_naim_yul_on_root() -> None:
    """Старый формат: имя ЮЛ-учредителя как атрибут на сам теге `УчрЮЛРос`."""
    el = _xml(
        '<СвЮЛ><СведУчредит>'
        '<УчрЮЛРос НаимЮЛ="Старый холдинг"/>'
        '</СведУчредит></СвЮЛ>'
    )
    founders = _parse_founders(el)
    assert len(founders) == 1
    assert founders[0]["type"] == "legal"
    assert founders[0]["name"] == "Старый холдинг"


def test_parse_founders_person_with_share() -> None:
    el = _xml(
        "<СвЮЛ><СведУчредит>"
        "<УчрФЛ>"
        '<СвФЛ ФамилияРус="Смирнов" ИмяРус="С" ИННФЛ="500100732259"/>'
        '<ДоляУстКап РазмерДоли="100" НоминСтоим="10000"/>'
        "</УчрФЛ>"
        "</СведУчредит></СвЮЛ>"
    )
    founders = _parse_founders(el)
    assert len(founders) == 1
    assert founders[0]["type"] == "person"
    assert founders[0]["name"] == "Смирнов С"
    assert founders[0]["inn"] == "500100732259"
    assert founders[0]["share_percent"] == 100.0
    assert founders[0]["share_sum"] == 10000.0


def test_parse_share_returns_zero_when_no_dolya() -> None:
    el = _xml("<УчрФЛ/>")
    percent, sum_val = _parse_share(el)
    assert percent == 0.0
    assert sum_val is None


def test_parse_share_invalid_percent_and_sum_degrade_safely() -> None:
    el = _xml('<УчрФЛ><ДоляУстКап РазмерДоли="abc" НоминСтоим="xyz"/></УчрФЛ>')
    percent, sum_val = _parse_share(el)
    assert percent == 0.0
    assert sum_val is None


def test_parse_share_clamps_percent_above_100() -> None:
    el = _xml('<УчрФЛ><ДоляУстКап РазмерДоли="150"/></УчрФЛ>')
    percent, _ = _parse_share(el)
    assert percent == 100.0


def test_parse_share_clamps_negative_percent_to_zero() -> None:
    el = _xml('<УчрФЛ><ДоляУстКап РазмерДоли="-5"/></УчрФЛ>')
    percent, _ = _parse_share(el)
    assert percent == 0.0


def test_parse_share_accepts_legacy_nominal_attribute() -> None:
    el = _xml('<УчрФЛ><ДоляУстКап Процент="25" Номинал="2500"/></УчрФЛ>')
    percent, sum_val = _parse_share(el)
    assert percent == 25.0
    assert sum_val == 2500.0


# ---------------------------------------------------------------------------
# _parse_fio_element + _extract_inn.
# ---------------------------------------------------------------------------


def test_parse_fio_element_returns_none_for_none_input() -> None:
    assert _parse_fio_element(None) is None


def test_parse_fio_element_returns_none_for_empty_element() -> None:
    el = _xml("<Dummy/>")
    assert _parse_fio_element(el) is None


def test_parse_fio_element_uses_legacy_attributes() -> None:
    el = _xml('<Dummy Фамилия="И" Имя="И" Отчество="И"/>')
    assert _parse_fio_element(el) == "И И И"


def test_extract_inn_direct_attribute() -> None:
    el = _xml('<Dummy ИНН="7707083893"/>')
    assert _extract_inn(el) == "7707083893"


def test_extract_inn_legacy_innfl_attribute() -> None:
    el = _xml('<Dummy ИННФЛ="500100732259"/>')
    assert _extract_inn(el) == "500100732259"


def test_extract_inn_from_child_svfl() -> None:
    el = _xml('<Dummy><СвФЛ ИННФЛ="500100732259"/></Dummy>')
    assert _extract_inn(el) == "500100732259"


def test_extract_inn_returns_none_when_absent() -> None:
    el = _xml("<Dummy/>")
    assert _extract_inn(el) is None


# ---------------------------------------------------------------------------
# ИП: _parse_ie_fio / _parse_ie_citizenship / _parse_ie_status.
# ---------------------------------------------------------------------------


def test_ie_status_map_contains_canonical_slugs() -> None:
    """Регрессионный: маппинг ИП не должен терять active/closed."""
    assert _IE_STATUS_MAP["действующий"] == "active"
    assert _IE_STATUS_MAP["прекращено"] == "closed"


def test_parse_ie_fio_raises_when_no_name() -> None:
    el = _xml("<СвИП><СвФЛ/></СвИП>")
    with pytest.raises(_RecordSkipped, match="ФамилияРус"):
        _parse_ie_fio(el, record_id="x")


def test_parse_ie_fio_falls_back_to_root_attributes() -> None:
    el = _xml('<СвИП ФамилияРус="Сидоров" ИмяРус="Сергей"/>')
    assert _parse_ie_fio(el, record_id="x") == "Сидоров Сергей"


def test_parse_ie_citizenship_none_when_no_element() -> None:
    el = _xml("<СвИП/>")
    assert _parse_ie_citizenship(el) is None


def test_parse_ie_citizenship_none_when_no_code() -> None:
    el = _xml("<СвИП><СвГраждФЛ/></СвИП>")
    assert _parse_ie_citizenship(el) is None


def test_parse_ie_citizenship_oksm_643_is_russia() -> None:
    el = _xml('<СвИП><СвГраждФЛ ОКСМ="643"/></СвИП>')
    assert _parse_ie_citizenship(el) == "RU"


def test_parse_ie_citizenship_foreign_is_other() -> None:
    el = _xml('<СвИП><СвГраждФЛ ОКСМ="276"/></СвИП>')
    assert _parse_ie_citizenship(el) == "other"


def test_parse_ie_status_legacy_svprekrip_marker_closed() -> None:
    el = _xml('<СвИП><СвПрекрИП ДатаПрекрИП="12.09.2024"/></СвИП>')
    assert _parse_ie_status(el, record_id="x") == "closed"


def test_parse_ie_status_active_default_without_svstatus() -> None:
    el = _xml("<СвИП/>")
    assert _parse_ie_status(el, record_id="x") == "active"


def test_parse_ie_status_legacy_status_attribute() -> None:
    el = _xml('<СвИП><СвСтатус СтатусИП="Действующий"/></СвИП>')
    assert _parse_ie_status(el, record_id="x") == "active"


def test_parse_ie_status_unknown_raises() -> None:
    el = _xml('<СвИП><СвСтатус НаимСтатусИП="Марсианский"/></СвИП>')
    with pytest.raises(_RecordSkipped, match="Неизвестный статус"):
        _parse_ie_status(el, record_id="x")


def test_parse_ie_close_date_returns_none_without_prekr() -> None:
    el = _xml("<СвИП/>")
    assert _parse_ie_close_date(el, record_id="x") is None


def test_parse_ie_close_date_reads_from_prekr_child() -> None:
    el = _xml('<СвИП><СвПрекрИП ДатаПрекрИП="12.09.2024"/></СвИП>')
    assert _parse_ie_close_date(el, record_id="x") == "2024-09-12"


# ---------------------------------------------------------------------------
# Sanity-check: внутренний маркер источника всё ещё 'opendata'.
# ---------------------------------------------------------------------------


def test_internal_data_source_constant_stays_opendata() -> None:
    """Регрессионный: попытка случайного rename под FastAPI-адаптер не должна пройти."""
    assert _DATA_SOURCE_OPENDATA == "opendata"


# ---------------------------------------------------------------------------
# Полный цикл `_parse_ie` + `_parse_company` на синтетических элементах
# — убеждаемся что выход действительно совместим с SQLiteStore upsert-контрактом
# (для этого достаточно убедиться в наборе ключей).
# ---------------------------------------------------------------------------


def test_parse_company_returns_contract_keys_for_sqlite_upsert() -> None:
    el = _xml(
        '<СвЮЛ ОГРН="1027700132195" ИНН="7707083893" КПП="773601001" '
        'ОКПО="12345678" НаимЛат="Sberbank" ДатаОГРН="20.03.1991">'
        '<СвНаимЮЛ НаимЮЛПолн="ПАО СБЕРБАНК РОССИИ">'
        '<СвНаимЮЛСокр НаимСокр="ПАО СБЕРБАНК"/>'
        '</СвНаимЮЛ>'
        '<СвСтатус НаимСтатусЮЛ="Действующее"/>'
        "</СвЮЛ>"
    )
    rec = _parse_company(el)
    expected_keys = {
        "inn",
        "ogrn",
        "kpp",
        "okpo",
        "name_short",
        "name_full",
        "name_latin",
        "status",
        "registered_at",
        "liquidated_at",
        "address_legal",
        "okved_main_code",
        "okved_main_description",
        "authorized_capital",
        "last_report_year",
        "data_json",
    }
    assert expected_keys <= set(rec.keys())
    assert rec["okpo"] == "12345678"
    assert rec["name_latin"] == "Sberbank"


def test_parse_ie_returns_contract_keys_for_sqlite_upsert() -> None:
    el = _xml(
        '<СвИП ОГРНИП="304500116000061" ИННФЛ="500100732259" ДатаОГРНИП="15.01.2004">'
        '<СвФЛ ФамилияРус="Иванов" ИмяРус="Иван" ОтчествоРус="Иванович"/>'
        '<СвГраждФЛ ОКСМ="643"/>'
        '<СвСтатус НаимСтатусИП="Действующий"/>'
        "</СвИП>"
    )
    rec = _parse_ie(el)
    expected_keys = {
        "ogrnip",
        "inn",
        "fio",
        "citizenship",
        "status",
        "registered_at",
        "closed_at",
        "okved_main_code",
        "okved_main_description",
        "data_json",
    }
    assert expected_keys <= set(rec.keys())
    assert rec["status"] == "active"
    assert rec["citizenship"] == "RU"


# ---------------------------------------------------------------------------
# Defence in depth: _parse_company должен выставлять source/source_date из ParsedRecord-wrapper'а
# (через iter_dump_records, но на уровне unit — проверяем через _parse_company).
# ---------------------------------------------------------------------------


def test_parse_company_date_parsed_correctly() -> None:
    el = _xml(
        '<СвЮЛ ОГРН="1027700132195" ИНН="7707083893" ДатаОГРН="20.03.1991">'
        '<СвНаимЮЛ НаимЮЛПолн="ООО Тест"/>'
        '<СвСтатус НаимСтатусЮЛ="Действующее"/>'
        "</СвЮЛ>"
    )
    rec = _parse_company(el)
    assert rec["registered_at"] == date(1991, 3, 20).isoformat()
