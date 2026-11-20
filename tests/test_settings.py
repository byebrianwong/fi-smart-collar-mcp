from fi_mcp import health, settings

from .test_health import TODAY, _days

PEPPER = {"id": "pet-2", "name": "Pepper"}


def test_missing_file_means_no_settings():
    assert settings.for_pet(PEPPER) == settings.DogSettings()


def test_matches_name_in_any_case(settings_file):
    settings_file.write_text("[dogs.pepper]\ncollar_off_at_night = true\n")
    assert settings.for_pet(PEPPER).collar_off_at_night is True
    assert settings.for_pet({"id": "pet-1", "name": "Biscuit"}).collar_off_at_night is False


def test_matches_fi_pet_id(settings_file):
    settings_file.write_text('[dogs."pet-2"]\ncollar_off_at_night = true\n')
    assert settings.for_pet(PEPPER).collar_off_at_night is True


def test_broken_file_is_ignored(settings_file):
    settings_file.write_text("[dogs.Pepper\ncollar_off_at_night = true\n")
    assert settings.for_pet(PEPPER) == settings.DogSettings()


def test_only_true_turns_it_on(settings_file):
    settings_file.write_text('[dogs.Pepper]\ncollar_off_at_night = "yes"\n')
    assert settings.for_pet(PEPPER).collar_off_at_night is False


def test_compare_can_leave_rest_unflagged():
    days = _days({})
    for d in days[1:8]:
        d["sleep_minutes"], d["nap_minutes"], d["rest_minutes"] = 0, 450, 450  # nights missing
    full = health.compare(days, TODAY)
    assert any(f.startswith("Total rest") for f in full["flags"])
    no_rest = health.compare(days, TODAY, flagged=("steps", "distance_km"))
    assert no_rest["flags"] == []
    # The numbers are still reported.
    assert no_rest["recent_vs_baseline"]["rest_minutes"]["change_percent"] == -40
