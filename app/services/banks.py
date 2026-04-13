from dataclasses import dataclass


@dataclass(frozen=True)
class BankOption:
    id: str
    label: str
    enabled: bool


@dataclass(frozen=True)
class BankParserProfile:
    id: str
    label: str
    enabled: bool
    processor_env_name: str
    text_rule_set: str
    document_rule_set: str
    recommend_ocr_when_empty: bool = True


DEFAULT_BANK_ID = "fnb"

_ALIASES: dict[str, str] = {
    "standardbank": "standard_bank",
    "capitecpersonal": "capitec_personal",
    "nedbank": "netbank",
}

_BANK_PROFILES: tuple[BankParserProfile, ...] = (
    BankParserProfile(
        id="fnb",
        label="FNB",
        enabled=True,
        processor_env_name="DOCUMENTAI_PROCESSOR_ID",
        text_rule_set="fnb",
        document_rule_set="fnb_layout",
    ),
    BankParserProfile(
        id="capitec",
        label="Capitec Business",
        enabled=True,
        processor_env_name="DOCUMENTAI_PROCESSOR_ID_CAPITEC",
        text_rule_set="capitec",
        document_rule_set="text_fallback",
    ),
    BankParserProfile(
        id="capitec_personal",
        label="Capitec Personal",
        enabled=True,
        processor_env_name="DOCUMENTAI_PROCESSOR_ID_CAPITEC_PERSONAL",
        text_rule_set="capitec_personal",
        document_rule_set="text_fallback",
    ),
    BankParserProfile(
        id="standard_bank",
        label="Standard Bank",
        enabled=True,
        processor_env_name="DOCUMENTAI_PROCESSOR_ID_STANDARD_BANK",
        text_rule_set="standard_bank",
        document_rule_set="text_fallback",
    ),
    BankParserProfile(
        id="absa",
        label="Absa (Not working yet)",
        enabled=False,
        processor_env_name="DOCUMENTAI_PROCESSOR_ID_ABSA",
        text_rule_set="generic",
        document_rule_set="text_fallback",
    ),
    BankParserProfile(
        id="netbank",
        label="Netbank (Not working yet)",
        enabled=False,
        processor_env_name="DOCUMENTAI_PROCESSOR_ID_NETBANK",
        text_rule_set="generic",
        document_rule_set="text_fallback",
    ),
    BankParserProfile(
        id="all_other_sa_banks",
        label="All other SA banks (Not working yet)",
        enabled=False,
        processor_env_name="DOCUMENTAI_PROCESSOR_ID",
        text_rule_set="generic",
        document_rule_set="text_fallback",
    ),
)

_PROFILE_BY_ID: dict[str, BankParserProfile] = {profile.id: profile for profile in _BANK_PROFILES}

KNOWN_BANK_IDS: set[str] = set(_PROFILE_BY_ID.keys())
ENABLED_BANK_IDS: set[str] = {profile.id for profile in _BANK_PROFILES if profile.enabled}


def list_bank_options() -> tuple[BankOption, ...]:
    return tuple(BankOption(id=p.id, label=p.label, enabled=p.enabled) for p in _BANK_PROFILES)


def normalize_bank_id(raw_value: str | None) -> str:
    value = (raw_value or "").strip().lower()
    if not value:
        return DEFAULT_BANK_ID
    normalized = _ALIASES.get(value, value)
    if normalized not in KNOWN_BANK_IDS:
        return DEFAULT_BANK_ID
    return normalized


def get_bank_profile(raw_value: str | None) -> BankParserProfile:
    normalized = normalize_bank_id(raw_value)
    return _PROFILE_BY_ID.get(normalized, _PROFILE_BY_ID[DEFAULT_BANK_ID])


def get_enabled_bank_profile(raw_value: str | None) -> BankParserProfile:
    profile = get_bank_profile(raw_value)
    if profile.enabled:
        return profile
    return _PROFILE_BY_ID[DEFAULT_BANK_ID]


def coerce_enabled_bank_id(raw_value: str | None) -> str:
    return get_enabled_bank_profile(raw_value).id
