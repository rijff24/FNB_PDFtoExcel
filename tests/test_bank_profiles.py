from app.services.banks import (
    DEFAULT_BANK_ID,
    coerce_enabled_bank_id,
    get_bank_profile,
    get_enabled_bank_profile,
    list_bank_options,
)


def test_bank_profile_aliases_and_enabled_coercion() -> None:
    assert get_bank_profile("capitecpersonal").id == "capitec_personal"
    assert get_bank_profile("standardbank").id == "standard_bank"
    assert get_bank_profile("nedbank").id == "netbank"
    assert get_enabled_bank_profile("absa").id == DEFAULT_BANK_ID
    assert coerce_enabled_bank_id("unknown_bank") == DEFAULT_BANK_ID


def test_bank_processor_env_names_for_active_profiles() -> None:
    assert get_enabled_bank_profile("fnb").processor_env_name == "DOCUMENTAI_PROCESSOR_ID"
    assert get_enabled_bank_profile("capitec").processor_env_name == "DOCUMENTAI_PROCESSOR_ID_CAPITEC"
    assert (
        get_enabled_bank_profile("capitec_personal").processor_env_name
        == "DOCUMENTAI_PROCESSOR_ID_CAPITEC_PERSONAL"
    )
    assert (
        get_enabled_bank_profile("standard_bank").processor_env_name
        == "DOCUMENTAI_PROCESSOR_ID_STANDARD_BANK"
    )


def test_list_bank_options_contains_enabled_and_disabled() -> None:
    options = list_bank_options()
    option_ids = {option.id for option in options}
    assert {"fnb", "capitec", "capitec_personal", "standard_bank"}.issubset(option_ids)
    assert {"absa", "netbank", "all_other_sa_banks"}.issubset(option_ids)
