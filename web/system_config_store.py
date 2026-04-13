import logging

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from web.models import SystemConfig

log = logging.getLogger(__name__)

_SCHEMA_DRIFT_MARKER = "_schema_drift_fallback"
_SYSTEM_CONFIG_TABLE = "system_config"


def _default_for_column(column) -> object | None:
    default = column.default
    if default is None:
        return None
    arg = default.arg
    if callable(arg):
        try:
            return arg()
        except TypeError:
            return None
    return arg


def _system_config_defaults() -> SystemConfig:
    sys_conf = SystemConfig()
    for column in SystemConfig.__table__.columns:
        if getattr(sys_conf, column.key, None) is not None:
            continue
        default = _default_for_column(column)
        if default is not None:
            setattr(sys_conf, column.key, default)
    return sys_conf


def _mark_schema_drift(sys_conf: SystemConfig) -> SystemConfig:
    setattr(sys_conf, _SCHEMA_DRIFT_MARKER, True)
    return sys_conf


def system_config_schema_is_behind(sys_conf: SystemConfig | None) -> bool:
    return bool(sys_conf and getattr(sys_conf, _SCHEMA_DRIFT_MARKER, False))


def _is_system_config_schema_drift(exc: SQLAlchemyError) -> bool:
    message = str(exc).lower()
    if _SYSTEM_CONFIG_TABLE not in message:
        return False
    return any(
        needle in message
        for needle in (
            "undefinedcolumn",
            "does not exist",
            "no such column",
            "has no column named",
            "unknown column",
        )
    )


def _existing_system_config_columns(db: Session) -> set[str]:
    inspector = sa.inspect(db.connection())
    return {col["name"] for col in inspector.get_columns(_SYSTEM_CONFIG_TABLE)}


def _load_system_config_fallback(db: Session, exc: SQLAlchemyError) -> SystemConfig:
    db.rollback()
    try:
        from web.db import db_migrate
        db_migrate()
        db.rollback()
        try:
            repaired = db.query(SystemConfig).first()
        except SQLAlchemyError as retry_exc:
            if not _is_system_config_schema_drift(retry_exc):
                raise
            db.rollback()
            log.warning("SystemConfig schema repair did not fully resolve drift: %s", retry_exc)
        else:
            return repaired
    except Exception as repair_exc:
        db.rollback()
        log.warning("SystemConfig schema repair failed: %s", repair_exc)

    sys_conf = _system_config_defaults()
    try:
        existing_columns = _existing_system_config_columns(db)
    except Exception as inspect_exc:
        log.warning("SystemConfig schema drift detected and inspection failed: %s", inspect_exc)
        return _mark_schema_drift(sys_conf)

    selectable = [
        SystemConfig.__table__.c[column_name]
        for column_name in SystemConfig.__table__.columns.keys()
        if column_name in existing_columns
    ]
    if selectable:
        row = db.execute(sa.select(*selectable).limit(1)).mappings().first()
        if row:
            for key, value in row.items():
                setattr(sys_conf, key, value)

    log.warning("SystemConfig schema drift detected; using compatibility defaults: %s", exc)
    return _mark_schema_drift(sys_conf)


def load_system_config(db: Session, *, create_if_missing: bool = False) -> SystemConfig | None:
    try:
        sys_conf = db.query(SystemConfig).first()
    except SQLAlchemyError as exc:
        if not _is_system_config_schema_drift(exc):
            raise
        return _load_system_config_fallback(db, exc)

    if not sys_conf and create_if_missing:
        sys_conf = SystemConfig()
        db.add(sys_conf)
        db.commit()
        db.refresh(sys_conf)
    return sys_conf
