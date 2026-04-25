"""Потоковый XML-парсер дампов ФНС ЕГРЮЛ и ЕГРИП.

Работает по **канонической схеме ФНС** (XSD публикуется на data.nalog.ru):

Корневые элементы:
    * `<ЕГРЮЛ>` содержит повторяющиеся `<СвЮЛ>` (одна запись — одно юр.лицо).
    * `<ЕГРИП>` содержит повторяющиеся `<СвИП>` (одна запись — один ИП).

Любой неизвестный статус, невалидный ИНН или отсутствующий обязательный
атрибут — НЕ silent fallback, а явный `McpEgrulError` в отчёте, чтобы вверху
это можно было чётко увидеть.

Реализация потоковая: `lxml.etree.iterparse` с `event=('end',)` и очисткой
элементов после обработки — памяти расходуется ~O(1) на архив.

Вход:
    * `.xml`-файл напрямую, или
    * `.zip`-архив, внутри которого один или несколько `.xml`.

Выход:
    `parse_egrul_source(path)` → генератор `ParsedRecord` — dict в формате
    `SQLiteStore.upsert_company/.upsert_ie`.
"""

from __future__ import annotations

import io
import logging
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import date as DateT
from pathlib import Path
from typing import Any

from lxml import etree

from ..constants import DATA_SOURCES, IMPORT_FNS_DATE_FORMAT
from ..errors import McpEgrulError
from ..validators import is_valid_inn, is_valid_ogrn

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Константы схемы (русские теги / атрибуты ФНС).
# ---------------------------------------------------------------------------

# Верхнеуровневые контейнеры записей.
_TAG_COMPANY: str = "СвЮЛ"
_TAG_IE: str = "СвИП"

# Статусы компаний — явный словарь без эвристик. Любой неизвестный статус
# считается ошибкой записи, чтобы не превратить её в "active" по умолчанию.
_COMPANY_STATUS_MAP: dict[str, str] = {
    "действующее": "active",
    "действующий": "active",
    "в стадии ликвидации": "liquidating",
    "находится в процессе ликвидации": "liquidating",
    "ликвидировано": "liquidated",
    "прекратило деятельность при реорганизации": "liquidated",
    "прекратило деятельность (исключение из егрюл недействующего юл)": "liquidated",
    "прекратило деятельность": "liquidated",
    "в стадии реорганизации": "reorganizing",
    "находится в процессе реорганизации": "reorganizing",
    "находится в стадии реорганизации": "reorganizing",
    "в отношении юл введено конкурсное производство": "bankrupt",
    "в отношении юл введено наблюдение": "bankrupt",
    "в отношении юл введено внешнее управление": "bankrupt",
    "признано банкротом": "bankrupt",
}

_IE_STATUS_MAP: dict[str, str] = {
    "действующий": "active",
    "действующее": "active",
    "прекращено": "closed",
    "прекратил деятельность": "closed",
    "прекратил деятельность в качестве индивидуального предпринимателя": "closed",
}

_FOUNDER_TAG_TO_TYPE: dict[str, str] = {
    "УчрФЛ": "person",
    "УчрЮЛРос": "legal",
    "УчрЮЛИн": "legal",
    "УчрРФСубМО": "legal",
    "УчрПИФ": "legal",
}

_DATA_SOURCE_OPENDATA: str = "opendata"
assert _DATA_SOURCE_OPENDATA in DATA_SOURCES, (
    "DATA_SOURCES must contain 'opendata' — validated at import time"
)

# Событие lxml, которое мы слушаем (конец элемента).
_EVENT_END: str = "end"


# ---------------------------------------------------------------------------
# Типы.
# ---------------------------------------------------------------------------


@dataclass
class ParseError:
    """Одна ошибка парсинга конкретной записи (не валит весь ингест)."""

    registry: str
    record_id: str | None
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParseStats:
    """Статистика одного прохода парсера по файлу/архиву."""

    records_parsed: int = 0
    records_yielded: int = 0
    errors: list[ParseError] = field(default_factory=list)

    def record_error(self, err: ParseError) -> None:
        self.errors.append(err)


# Каждая запись — dict в формате `SQLiteStore.upsert_*` + дополнительные поля.
ParsedRecord = dict[str, Any]


# ---------------------------------------------------------------------------
# Публичные функции.
# ---------------------------------------------------------------------------


def iter_dump_records(
    path: Path,
    *,
    registry: str,
    source_date: DateT,
    stats: ParseStats,
) -> Iterator[ParsedRecord]:
    """Перечислить записи одного дампа (xml или zip).

    Args:
        path: путь к `.xml` или `.zip`.
        registry: 'egrul' или 'egrip' — определяет, какой парсер применить.
        source_date: дата выгрузки дампа ФНС (для поля `source_date` в БД).
        stats: сюда складываются ошибки и счётчики (in-place).

    Yields:
        `ParsedRecord` готовый к `SQLiteStore.upsert_company/.upsert_ie`.
    """
    if registry == "egrul":
        parse_fn = _parse_company
        target_tag = _TAG_COMPANY
    elif registry == "egrip":
        parse_fn = _parse_ie
        target_tag = _TAG_IE
    else:
        raise McpEgrulError(
            f"Неизвестный реестр '{registry}'. Ожидается 'egrul' или 'egrip'.",
            details={"registry": registry},
        )

    for xml_stream in _open_xml_streams(path):
        yield from _iter_records_from_stream(
            xml_stream,
            target_tag=target_tag,
            parse_fn=parse_fn,
            registry=registry,
            source_date=source_date,
            stats=stats,
        )


def _open_xml_streams(path: Path) -> Iterator[io.IOBase]:
    """Открыть XML-файлы из `path` (xml или zip с несколькими xml)."""
    if not path.exists():
        raise McpEgrulError(
            f"Файл дампа не найден: {path}",
            details={"path": str(path)},
        )

    if path.is_dir():
        raise McpEgrulError(
            f"Ожидается файл, а не директория: {path}",
            hint="Передавайте конкретный .xml или .zip, не папку.",
            details={"path": str(path)},
        )

    suffix = path.suffix.lower()
    if suffix == ".xml":
        with path.open("rb") as f:
            yield f
        return

    if suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            xml_members = [m for m in zf.namelist() if m.lower().endswith(".xml")]
            if not xml_members:
                raise McpEgrulError(
                    f"В архиве {path.name} нет .xml файлов.",
                    details={"path": str(path), "members": zf.namelist()[:10]},
                )
            for member in xml_members:
                with zf.open(member) as f:
                    yield f
        return

    raise McpEgrulError(
        f"Неподдерживаемое расширение файла дампа: {suffix!r}.",
        hint="Ожидается .xml или .zip с .xml внутри.",
        details={"path": str(path)},
    )


def _iter_records_from_stream(
    xml_stream: io.IOBase,
    *,
    target_tag: str,
    parse_fn: Callable[[etree._Element], ParsedRecord],
    registry: str,
    source_date: DateT,
    stats: ParseStats,
) -> Iterator[ParsedRecord]:
    """Стримингово пройтись по XML, выбирая элементы с именем `target_tag`."""
    context = etree.iterparse(
        xml_stream,
        events=(_EVENT_END,),
        tag=target_tag,
        recover=False,
        remove_blank_text=True,
    )

    for _event, element in context:
        stats.records_parsed += 1
        try:
            record = parse_fn(element)
            record["source"] = _DATA_SOURCE_OPENDATA
            record["source_date"] = source_date.isoformat()
            record["updated_at"] = datetime.now(tz=UTC).isoformat()
            stats.records_yielded += 1
            yield record
        except _RecordSkipped as skip:
            stats.record_error(
                ParseError(
                    registry=registry,
                    record_id=skip.record_id,
                    message=skip.message,
                    details=skip.details,
                )
            )
        finally:
            element.clear()
            parent = element.getparent()
            if parent is not None:
                while element.getprevious() is not None:
                    del parent[0]

    del context


# ---------------------------------------------------------------------------
# Парсеры сущностей.
# ---------------------------------------------------------------------------


class _RecordSkipped(Exception):
    """Внутренний сигнал: запись пропустить, но ингест продолжить."""

    def __init__(
        self,
        message: str,
        *,
        record_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.record_id = record_id
        self.details = details or {}


def _parse_company(el: etree._Element) -> ParsedRecord:
    """Распарсить `<СвЮЛ>` в ParsedRecord для `companies`."""
    inn = _require_attr(el, "ИНН", record_id=None)
    ogrn = _require_attr(el, "ОГРН", record_id=inn)

    if not is_valid_inn(inn) or len(inn) != 10:
        raise _RecordSkipped(
            f"ИНН юр.лица '{inn}' не прошёл проверку контрольной цифры.",
            record_id=ogrn,
            details={"inn": inn, "ogrn": ogrn},
        )
    if not is_valid_ogrn(ogrn) or len(ogrn) != 13:
        raise _RecordSkipped(
            f"ОГРН '{ogrn}' не прошёл проверку контрольной цифры.",
            record_id=ogrn,
            details={"inn": inn, "ogrn": ogrn},
        )

    kpp = el.get("КПП")
    if kpp is not None and len(kpp) != 9:
        raise _RecordSkipped(
            f"КПП '{kpp}' имеет длину != 9 цифр.",
            record_id=ogrn,
            details={"inn": inn, "kpp": kpp},
        )

    name_full, name_short = _parse_company_names(el, record_id=ogrn)
    status = _parse_company_status(el, record_id=ogrn)
    registered_at = _parse_iso_date_attr(
        el, "ДатаОГРН", required=True, record_id=ogrn
    )
    liquidated_at = _parse_iso_date_attr(
        el, "ДатаПрекрЮЛ", required=False, record_id=ogrn
    )

    address_legal = _parse_address_raw(el)
    okved_main, okved_additional = _parse_okved_block(el)
    authorized_capital = _parse_authorized_capital(el)
    director = _parse_director(el)
    founders = _parse_founders(el)

    data_json: dict[str, Any] = {
        "okved_additional": [o for o in okved_additional],
    }
    if director is not None:
        data_json["director"] = director
    if founders:
        data_json["founders"] = founders

    return {
        "inn": inn,
        "ogrn": ogrn,
        "kpp": kpp,
        "okpo": el.get("ОКПО"),
        "name_short": name_short or name_full,
        "name_full": name_full,
        "name_latin": el.get("НаимЛат"),
        "status": status,
        "registered_at": registered_at,
        "liquidated_at": liquidated_at,
        "address_legal": address_legal,
        "okved_main_code": (okved_main or {}).get("code"),
        "okved_main_description": (okved_main or {}).get("description"),
        "authorized_capital": authorized_capital,
        "last_report_year": None,
        "data_json": data_json,
    }


def _parse_ie(el: etree._Element) -> ParsedRecord:
    """Распарсить `<СвИП>` в ParsedRecord для `individual_entrepreneurs`."""
    ogrnip = _require_attr(el, "ОГРНИП", record_id=None)
    inn = _require_attr(el, "ИННФЛ", record_id=ogrnip)

    if not is_valid_ogrn(ogrnip) or len(ogrnip) != 15:
        raise _RecordSkipped(
            f"ОГРНИП '{ogrnip}' не прошёл проверку контрольной цифры.",
            record_id=ogrnip,
        )
    if not is_valid_inn(inn) or len(inn) != 12:
        raise _RecordSkipped(
            f"ИНН ИП '{inn}' не прошёл проверку контрольной цифры.",
            record_id=ogrnip,
        )

    fio = _parse_ie_fio(el, record_id=ogrnip)
    citizenship = _parse_ie_citizenship(el)
    status = _parse_ie_status(el, record_id=ogrnip)
    registered_at = _parse_iso_date_attr(
        el, "ДатаОГРНИП", required=True, record_id=ogrnip
    )
    closed_at = _parse_ie_close_date(el, record_id=ogrnip)
    okved_main, okved_additional = _parse_okved_block(el)

    data_json: dict[str, Any] = {"okved_additional": list(okved_additional)}

    return {
        "ogrnip": ogrnip,
        "inn": inn,
        "fio": fio,
        "citizenship": citizenship,
        "status": status,
        "registered_at": registered_at,
        "closed_at": closed_at,
        "okved_main_code": (okved_main or {}).get("code"),
        "okved_main_description": (okved_main or {}).get("description"),
        "data_json": data_json,
    }


# ---------------------------------------------------------------------------
# Хелперы.
# ---------------------------------------------------------------------------


def _require_attr(el: etree._Element, name: str, *, record_id: str | None) -> str:
    value = el.get(name)
    if not value:
        raise _RecordSkipped(
            f"В записи отсутствует обязательный атрибут {name}.",
            record_id=record_id,
            details={"attr": name, "tag": etree.QName(el.tag).localname},
        )
    return value


def _parse_iso_date_attr(
    el: etree._Element, name: str, *, required: bool, record_id: str | None
) -> str | None:
    raw = el.get(name)
    if raw is None or raw == "":
        if required:
            raise _RecordSkipped(
                f"Пустое обязательное поле даты {name}.",
                record_id=record_id,
                details={"attr": name},
            )
        return None
    try:
        parsed = datetime.strptime(raw, IMPORT_FNS_DATE_FORMAT).date()
    except ValueError as exc:
        raise _RecordSkipped(
            (
                f"Неожиданный формат даты в {name}='{raw}' "
                f"(ожидается {IMPORT_FNS_DATE_FORMAT})."
            ),
            record_id=record_id,
            details={"attr": name, "value": raw, "parse_error": str(exc)},
        ) from exc
    return parsed.isoformat()


def _parse_company_names(
    el: etree._Element, *, record_id: str
) -> tuple[str, str | None]:
    """Вернуть (name_full, name_short).

    Полное имя — атрибут `НаимЮЛПолн` на `<СвНаимЮЛ>` (либо на самом `<СвЮЛ>`,
    если дамп в старом формате). Сокращённое — атрибут `НаимСокр` на
    `<СвНаимЮЛСокр>`.
    """
    sv_name = el.find("СвНаимЮЛ")
    name_full: str | None = None
    name_short: str | None = None
    if sv_name is not None:
        name_full = sv_name.get("НаимЮЛПолн")
        sv_short = sv_name.find("СвНаимЮЛСокр")
        if sv_short is not None:
            name_short = sv_short.get("НаимСокр")
    if name_full is None:
        name_full = el.get("НаимЮЛПолн")
    if name_short is None:
        name_short = el.get("НаимЮЛСокр")
    if not name_full:
        raise _RecordSkipped(
            "Не заполнено полное наименование юр.лица (НаимЮЛПолн).",
            record_id=record_id,
        )
    return name_full, name_short


def _parse_company_status(el: etree._Element, *, record_id: str) -> str:
    """Вернуть slug статуса юр.лица.

    ФНС отдаёт статус двумя способами (оба встречаются в реальных дампах):
        1. Атрибут `НаимСтатусЮЛ` на `<СвСтатус>`.
        2. Вложенный `<СвСтатус СтатусЮЛ="..."/>` (старые дампы).
    """
    sv = el.find("СвСтатус")
    status_text: str | None = None
    if sv is not None:
        status_text = sv.get("НаимСтатусЮЛ") or sv.get("СтатусЮЛ")
    if not status_text:
        liquidated = el.get("ДатаПрекрЮЛ")
        return "liquidated" if liquidated else "active"
    key = status_text.strip().lower()
    if key in _COMPANY_STATUS_MAP:
        return _COMPANY_STATUS_MAP[key]
    raise _RecordSkipped(
        f"Неизвестный статус юр.лица '{status_text}' — маппинг не определён.",
        record_id=record_id,
        details={"status_text": status_text},
    )


def _parse_address_raw(el: etree._Element) -> str | None:
    addr = el.find("АдресЮЛ")
    if addr is None:
        return None
    parts: list[str] = []
    postal = addr.get("Индекс") or addr.get("Почтовый")
    if postal:
        parts.append(postal)
    for child_tag in ("НаимРегион", "Город", "НаселПункт", "Улица"):
        sub = addr.find(child_tag)
        if sub is not None:
            name = (
                sub.get("Наименование")
                or sub.get("Наим")
                or (sub.text.strip() if sub.text else None)
            )
            type_ = sub.get("ТипРегион") or sub.get("Тип")
            if type_ and name:
                parts.append(f"{type_} {name}")
            elif name:
                parts.append(name)
    if addr.get("Дом"):
        parts.append(f"д. {addr.get('Дом')}")
    if addr.get("Кварт"):
        parts.append(f"кв. {addr.get('Кварт')}")
    if not parts:
        raw = addr.get("АдрЮЛФИАС") or addr.get("Адрес")
        return raw
    return ", ".join(parts)


def _parse_okved_block(
    el: etree._Element,
) -> tuple[dict[str, str | None] | None, list[dict[str, str | None]]]:
    sv = el.find("СвОКВЭД")
    if sv is None:
        return None, []
    main_el = sv.find("СвОКВЭДОсн")
    okved_main: dict[str, str | None] | None = None
    if main_el is not None:
        okved_main = {
            "code": main_el.get("КодОКВЭД"),
            "description": main_el.get("НаимОКВЭД"),
        }
    additional: list[dict[str, str | None]] = []
    for dop_el in sv.iterfind("СвОКВЭДДоп"):
        additional.append(
            {
                "code": dop_el.get("КодОКВЭД"),
                "description": dop_el.get("НаимОКВЭД"),
            }
        )
    return okved_main, additional


def _parse_authorized_capital(el: etree._Element) -> float | None:
    cap_el = el.find("СвУстКап")
    if cap_el is None:
        cap_el = el.find("СвКапитал")
    if cap_el is None:
        return None
    raw = cap_el.get("СумКап") or cap_el.get("СумУстКап")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise _RecordSkipped(
            f"Невалидное значение уставного капитала: '{raw}'.",
            details={"attr": "СумКап", "value": raw, "parse_error": str(exc)},
        ) from exc


def _parse_director(el: etree._Element) -> dict[str, Any] | None:
    ruk = el.find("СвРуковод")
    if ruk is None:
        return None
    fio = _parse_fio_element(ruk.find("СвФЛ") if ruk.find("СвФЛ") is not None else ruk)
    if not fio:
        return None
    pos_el = ruk.find("СвДолжн")
    position = (
        pos_el.get("НаимДолжн")
        if pos_el is not None and pos_el.get("НаимДолжн")
        else "Руководитель"
    )
    director_inn = _extract_inn(ruk)
    return {
        "fio": fio,
        "position": position,
        "inn": director_inn,
    }


def _parse_founders(el: etree._Element) -> list[dict[str, Any]]:
    uchr_container = el.find("СведУчредит")
    if uchr_container is None:
        return []
    founders: list[dict[str, Any]] = []
    for child in uchr_container:
        local = etree.QName(child.tag).localname
        if local not in _FOUNDER_TAG_TO_TYPE:
            continue
        founder_type = _FOUNDER_TAG_TO_TYPE[local]
        if founder_type == "person":
            name = _parse_fio_element(
                child.find("СвФЛ") if child.find("СвФЛ") is not None else child
            )
            inn_val = _extract_inn(child)
        else:
            name_el = child.find("НаимИННЮЛ")
            name = name_el.get("НаимЮЛПолн") if name_el is not None else None
            inn_val = name_el.get("ИНН") if name_el is not None else None
            if not name:
                name = child.get("НаимЮЛ") or child.get("НаимЮЛПолн")
        if not name:
            continue
        share_percent, share_sum = _parse_share(child)
        founders.append(
            {
                "type": founder_type,
                "name": name,
                "inn": inn_val,
                "share_percent": share_percent,
                "share_sum": share_sum,
            }
        )
    return founders


def _parse_share(founder_el: etree._Element) -> tuple[float, float | None]:
    """Извлечь долю (%) и номинал доли (руб) — из `<ДоляУстКап>`.

    ФНС может передавать долю как процент (`РазмерДоли="50"`) или как дробь
    (`ДоляРубля Номинал="5000" Процент="50"`). Возвращаем (0, None) если нет.
    """
    dolya = founder_el.find("ДоляУстКап")
    if dolya is None:
        return 0.0, None
    percent_raw = dolya.get("РазмерДоли") or dolya.get("Процент")
    sum_raw = dolya.get("НоминСтоим") or dolya.get("Номинал")
    try:
        percent = float(percent_raw) if percent_raw else 0.0
    except (TypeError, ValueError):
        percent = 0.0
    try:
        sum_val: float | None = float(sum_raw) if sum_raw else None
    except (TypeError, ValueError):
        sum_val = None
    if percent > 100.0:
        percent = min(percent, 100.0)
    if percent < 0.0:
        percent = 0.0
    return percent, sum_val


def _parse_fio_element(el: etree._Element | None) -> str | None:
    if el is None:
        return None
    last = el.get("ФамилияРус") or el.get("Фамилия")
    first = el.get("ИмяРус") or el.get("Имя")
    middle = el.get("ОтчествоРус") or el.get("Отчество")
    parts = [p for p in (last, first, middle) if p]
    if not parts:
        return None
    return " ".join(parts)


def _extract_inn(parent: etree._Element) -> str | None:
    direct = parent.get("ИННФЛ") or parent.get("ИНН")
    if direct:
        return direct
    sv_fl = parent.find("СвФЛ")
    if sv_fl is not None:
        return sv_fl.get("ИННФЛ") or sv_fl.get("ИНН")
    return None


def _parse_ie_fio(el: etree._Element, *, record_id: str) -> str:
    sv_fl = el.find("СвФЛ")
    fio = _parse_fio_element(sv_fl if sv_fl is not None else el)
    if not fio:
        raise _RecordSkipped(
            "В записи ИП не заполнены ФамилияРус/ИмяРус.",
            record_id=record_id,
        )
    return fio


def _parse_ie_citizenship(el: etree._Element) -> str | None:
    sv = el.find("СвГраждФЛ")
    if sv is None:
        return None
    code = sv.get("КодГражд") or sv.get("ОКСМ")
    if not code:
        return None
    if code == "1" or code == "643":
        return "RU"
    return "other"


def _parse_ie_status(el: etree._Element, *, record_id: str) -> str:
    """Статус ИП: атрибут на `<СвСтатус>` или флаг `<СвПрекрИП>`."""
    if el.find("СвПрекрИП") is not None:
        return "closed"
    sv = el.find("СвСтатус")
    status_text: str | None = None
    if sv is not None:
        status_text = sv.get("НаимСтатусИП") or sv.get("СтатусИП")
    if not status_text:
        return "active"
    key = status_text.strip().lower()
    if key in _IE_STATUS_MAP:
        return _IE_STATUS_MAP[key]
    raise _RecordSkipped(
        f"Неизвестный статус ИП '{status_text}' — маппинг не определён.",
        record_id=record_id,
        details={"status_text": status_text},
    )


def _parse_ie_close_date(el: etree._Element, *, record_id: str) -> str | None:
    pre = el.find("СвПрекрИП")
    if pre is None:
        return None
    return _parse_iso_date_attr(
        pre, "ДатаПрекрИП", required=False, record_id=record_id
    )
