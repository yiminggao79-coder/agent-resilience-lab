# Local schedule helpers

This is a deliberately small pure-Python scheduling library. It stores no files,
contacts no service, and models one civil day per booking. Run the protected
suite with `python -m unittest discover -s tests`. Callers provide dictionaries;
public functions must leave them unchanged. `observe.py` is a protected public
observation boundary, not an implementation module. You may add your own tests.
