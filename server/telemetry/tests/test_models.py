import pytest
from pydantic import ValidationError

from app.models import Event, ImuBatch

from .helpers import batch, event


def test_nulls_accepted():
    b = batch(0)
    for s in b["samples"]:
        s.update(accel=None, gyro=None, mag=None, quat=None)
    assert ImuBatch.model_validate(b).samples[0].accel is None


def test_unknown_fields_ignored():
    b = batch(0, future_field=1)
    b["samples"][0]["extra"] = "x"
    assert ImuBatch.model_validate(b).seq == 0
    assert Event.model_validate({**event(1), "new": 2}).seq == 1


def test_missing_timestamp_rejected():
    b = batch(0)
    del b["samples"][0]["timestamp_ns"]
    with pytest.raises(ValidationError):
        ImuBatch.model_validate(b)


def test_unknown_event_name_stored_not_rejected():
    assert Event.model_validate(event(0, "brand_new_event")).event == "brand_new_event"
