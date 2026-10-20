from fundrag.collect.sources import normalize_url

BLK = "https://www.blackrock.com/gls-download/literature/kiid/"


def test_ishares_country_link_is_rewritten():
    nl = (
        "https://www.ishares.com/nl/particuliere-belegger/nl/literature/kiid/"
        "eu-priips-ishares-x-ucits-etf-usd-dist-ie00b1xnhc34-en.pdf"
    )
    assert normalize_url(nl) == BLK + "eu-priips-ishares-x-ucits-etf-usd-dist-ie00b1xnhc34-en.pdf"


def test_blackrock_and_other_links_are_left_alone():
    url = BLK + "eu-priips-ishares-core-msci-world-ucits-etf-ie00b4l5y983-en.pdf"
    assert normalize_url(url) == url
    assert normalize_url(" https://example.com/kid.pdf ") == "https://example.com/kid.pdf"
