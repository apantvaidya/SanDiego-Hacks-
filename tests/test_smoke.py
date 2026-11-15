def test_models_import():
    from app.models import LEVEL_ORDER, CareLevel
    assert LEVEL_ORDER[-1] is CareLevel.ER
