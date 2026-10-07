from app.models.requests import RepositorySearchRequest


def test_search_request_normalizes_filters():
    request = RepositorySearchRequest(
        packages=[" zfinance "],
        name_patterns=["z*", "y*"],
        object_types=["prog", "clas"],
    )
    assert request.packages == ["ZFINANCE"]
    assert request.name_patterns == ["Z*", "Y*"]
    assert request.object_types == ["PROG", "CLAS"]
