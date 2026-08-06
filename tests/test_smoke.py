from etl.helpers import norm_id

def test_norm_id():
    assert norm_id("authority", "US FDA") == "authority:us_fda"
