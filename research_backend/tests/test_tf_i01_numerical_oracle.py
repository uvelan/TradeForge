"""Run the dependency-free independent OHLC vectors in normal acceptance tests."""
from scripts.tf_i01_numerical_oracle import run


def test_independent_exact_indicator_vectors():
    evidence = run()
    periods = evidence["results"]
    p = 14
    sample = periods[str(p)]
    assert sample["adx"]["ADX"]["first_valid_bar"] == 2*p
    assert sample["adx"]["+DI"]["first_valid_bar"] == p+1
    assert sample["stochastic"]["%K"]["first_valid_bar"] == p
    assert sample["stochastic"]["%D"]["first_valid_bar"] == p+2
    assert sample["stochrsi"]["%K"]["first_valid_bar"] == 2*p
    assert sample["stochrsi"]["%D"]["first_valid_bar"] == 2*p+2
    assert sample["cci"]["CCI"]["first_valid_bar"] == p
    assert sample["keltner"]["Basis"]["first_valid_bar"] == p
    assert sample["flat SuperTrend"]["first_valid_bar"] == p
    assert periods["20"]["cci"]["CCI"]["first_valid_bar"] == 20
    assert periods["20"]["keltner"]["Basis"]["first_valid_bar"] == 20
    assert periods["10"]["flat SuperTrend"]["first_valid_bar"] == 10
    assert periods["flat boundary (p=200)"]["stochrsi"]["%D"]["first_valid_bar"] == 402
