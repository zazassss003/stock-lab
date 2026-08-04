"""Indicator functions: pure, stateless, no network, no future data.

Every function takes a DataFrame (or Series) and returns a Series aligned to
the same index. If a value at time t depends on data after t, it is a bug —
never use negative shifts or centred windows here.
"""
