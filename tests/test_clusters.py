from core.risk import load_clusters, cluster_of


def test_adani_entities_share_one_cluster():
    c = load_clusters()
    names = ["ADANIENSOL", "ADANIENT", "ADANIGREEN", "ADANIPORTS"]
    assert len({cluster_of(n, c) for n in names}) == 1


def test_power_theme_is_grouped():
    c = load_clusters()
    assert cluster_of("TATAPOWER", c) == cluster_of("CGPOWER", c)


def test_unmapped_symbol_is_its_own_cluster():
    assert cluster_of("UNKNOWNCO", load_clusters()) == "UNKNOWNCO"
