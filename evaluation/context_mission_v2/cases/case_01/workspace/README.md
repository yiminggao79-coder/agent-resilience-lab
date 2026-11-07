# Shared-outing expense helpers

Pure standard-library Python, with all amounts in integer cents. A positive
member balance means that member should receive money; a negative balance
means the member should send money. Negative expense amounts represent a
refund. Functions must preserve caller-owned lists and nested dictionaries.
Run `python3 -m unittest discover -s tests`. The original test and observe.py
are protected byte-for-byte; new separate tests are welcome.
